"""Evidently drift monitoring of the ticket classifier, plus the model quality it hides or reveals.

What is monitored. Raw text never enters the monitoring data: each ticket becomes a handful of
descriptors that the API could compute at inference time (length, words, share of words outside the
model's own vocabulary, non-letter and non-ASCII shares) plus the model's outputs (predicted queue,
route, confidence). The label is checked apart: in production it only arrives after the ticket is
resolved. Same rule as the API logs: no raw ticket text is retained (LGPD minimisation).

How drift is decided. Evidently's DataDriftPreset with the methods fixed instead of picked by sample
size: normed Wasserstein distance for numeric columns, Jensen-Shannon distance for categorical ones.
Dataset drift when >= 50% of the 8 serving columns drift. Both methods are effect sizes, so the
verdict does not flip with batch size the way p-values do.

Thresholds. Evidently's default (0.1 for every column) assumes independent tickets. Here tickets come
in paraphrase families, so two clean halves differ more than i.i.d. samples would (findings §8). Each
column's threshold is therefore calibrated on A/A splits of the test set (seeds 1000-1049, never used
for evaluation): the 95th percentile of the clean-vs-clean scores, never below Evidently's 0.1.

What is measured on top. Every scenario is repeated over 20 seeds (42-61): the control gives the
false-alarm rate (an A/A test), the others the detection rate, for both threshold regimes. Model
quality (F1 macro, accuracy, routing F1) is measured on each batch: drift is not degradation.

Run:
    uv run python -m support_copilot.drift.report             -> reports/_build/*.html + reports/drift_summary.md
    uv run python -m support_copilot.drift.report --publish   -> also copies the HTMLs to reports/
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, f1_score

from support_copilot.api.classifier import TicketClassifier
from support_copilot.drift.simulate import SEED, build_scenarios, is_german, load_inputs, split_indices
from support_copilot.guardrails.pii import strip_pii

logger = logging.getLogger(__name__)

NUMERIC = ["n_chars", "n_words", "oov_share", "non_letter_share", "non_ascii_share", "confidence"]
CATEGORICAL = ["pred_queue", "pred_route"]
SERVING = NUMERIC + CATEGORICAL  # known at inference time
TARGET = "label"  # known only after the ticket is resolved
MONITORED = [*SERVING, TARGET]
NUM_METHOD, CAT_METHOD, DEFAULT_THRESHOLD, DRIFT_SHARE = "wasserstein", "jensenshannon", 0.1, 0.5

CALIB_SEEDS = range(1000, 1050)
CALIB_QUANTILE = 0.95
N_SEEDS = 20
N_BOOT = 300
BUILD = Path("reports/_build")
SUMMARY = Path("reports/drift_summary.md")
THRESHOLDS = Path("reports/drift_thresholds.json")
# published file -> scenario (seed 42). The HTML embeds a UUID and a timestamp: publish deliberately (plan 4.7).
PUBLISH = {"drift_report.html": "language_de", "drift_report_control.html": "control"}


# --------------------------------------------------------------------------- descriptors


def describe(batch: pd.DataFrame, clf: TicketClassifier) -> pd.DataFrame:
    """Monitoring row per ticket: text descriptors + model outputs (+ label and route truth, kept apart)."""
    texts = [strip_pii(t) for t in batch["text"]]  # exactly what the classifier sees in the API
    proba = clf.pipeline.predict_proba(texts)
    pred = np.asarray(clf.labels)[proba.argmax(axis=1)]

    vec = clf.pipeline.named_steps["tfidf"]
    tokenize, preprocess = vec.build_tokenizer(), vec.build_preprocessor()
    vocab = {term for term in vec.vocabulary_ if " " not in term}

    rows = []
    for text in texts:
        tokens = tokenize(preprocess(text))
        visible = [c for c in text if not c.isspace()]
        letters = [c for c in visible if c.isalpha()]
        rows.append(
            {
                "n_chars": len(text),
                "n_words": len(text.split()),
                "oov_share": float(np.mean([t not in vocab for t in tokens])) if tokens else 1.0,
                "non_letter_share": 1 - len(letters) / len(visible) if visible else 0.0,
                "non_ascii_share": sum(not c.isascii() for c in letters) / len(letters) if letters else 0.0,
            }
        )
    out = pd.DataFrame(rows)
    out["confidence"] = proba.max(axis=1)
    out["pred_queue"] = pred
    out["pred_route"] = np.where(np.isin(pred, list(clf.technical)), "rag", "queue")
    out[TARGET] = batch["label"].to_numpy()
    out["is_technical"] = batch["is_technical"].to_numpy()
    return out


# --------------------------------------------------------------------------- Evidently


def _evidently() -> Any:
    os.environ.setdefault("EVIDENTLY_DISABLE_TELEMETRY", "1")  # no usage telemetry from a monitoring job
    import evidently

    return evidently


def run_report(reference: pd.DataFrame, current: pd.DataFrame, thresholds: dict[str, float] | None = None) -> Any:
    """Evidently snapshot: drift of the serving columns (preset) + label drift (delayed ground truth)."""
    ev = _evidently()
    from evidently.metrics import ValueDrift
    from evidently.presets import DataDriftPreset

    thr = {c: DEFAULT_THRESHOLD for c in MONITORED} | (thresholds or {})
    definition = ev.DataDefinition(numerical_columns=NUMERIC, categorical_columns=[*CATEGORICAL, TARGET])
    report = ev.Report(
        [
            DataDriftPreset(
                columns=SERVING,
                num_method=NUM_METHOD,
                cat_method=CAT_METHOD,
                per_column_threshold={c: thr[c] for c in SERVING},
                drift_share=DRIFT_SHARE,
            ),
            ValueDrift(column=TARGET, method=CAT_METHOD, threshold=thr[TARGET]),
        ]
    )
    return report.run(
        ev.Dataset.from_pandas(current[MONITORED].reset_index(drop=True), data_definition=definition),
        ev.Dataset.from_pandas(reference[MONITORED].reset_index(drop=True), data_definition=definition),
    )


@dataclass(frozen=True)
class ColumnDrift:
    method: str
    threshold: float
    score: float

    @property
    def drifted(self) -> bool:
        # p-value tests drift when p < threshold; distances (Wasserstein, Jensen-Shannon) when >= threshold
        if "p_value" in self.method:
            return self.score < self.threshold
        return self.score >= self.threshold


@dataclass(frozen=True)
class DriftResult:
    share: float  # as reported by Evidently's DriftedColumnsCount
    columns: dict[str, ColumnDrift] = field(default_factory=dict)

    @property
    def dataset_drift(self) -> bool:
        return self.share >= DRIFT_SHARE

    @property
    def drifted_serving(self) -> list[str]:
        return [c for c in SERVING if self.columns[c].drifted]

    @property
    def label_drift(self) -> bool:
        return self.columns[TARGET].drifted


def parse(snapshot: Any) -> DriftResult:
    """Pull the verdicts out of the snapshot (the same numbers the HTML shows)."""
    share, columns = float("nan"), {}
    for metric in snapshot.dict()["metrics"]:
        cfg = metric["config"]
        if cfg["type"].endswith("DriftedColumnsCount"):
            share = float(metric["value"]["share"])
        elif cfg["type"].endswith("ValueDrift"):
            columns[cfg["column"]] = ColumnDrift(cfg["method"], float(cfg["threshold"]), float(metric["value"]))
    return DriftResult(share=share, columns=columns)


def check_batch(
    reference: pd.DataFrame, current: pd.DataFrame, thresholds: dict[str, float] | None = None
) -> DriftResult:
    """Drift verdict for one described batch against the described reference."""
    return parse(run_report(reference, current, thresholds))


def calibrate(described_test: pd.DataFrame, test: pd.DataFrame) -> tuple[dict[str, float], pd.DataFrame]:
    """Per-column threshold = 95th percentile of clean-vs-clean scores (A/A), floored at Evidently's 0.1."""
    scores = []
    for seed in CALIB_SEEDS:
        ref_idx, cur_idx = split_indices(test, seed)
        res = check_batch(described_test.iloc[ref_idx], described_test.iloc[cur_idx])
        scores.append({c: res.columns[c].score for c in MONITORED})
    null = pd.DataFrame(scores)
    thresholds = {c: round(max(DEFAULT_THRESHOLD, float(null[c].quantile(CALIB_QUANTILE))), 3) for c in MONITORED}
    return thresholds, null


# --------------------------------------------------------------------------- model quality


def quality(described: pd.DataFrame, technical: frozenset[str], rng: np.random.Generator) -> dict[str, float]:
    y, p = described[TARGET].to_numpy(), described["pred_queue"].to_numpy()
    y_route, p_route = described["is_technical"].to_numpy(), np.isin(p, list(technical))
    boot = []
    for _ in range(N_BOOT):
        i = rng.integers(0, len(y), len(y))
        boot.append(f1_score(y[i], p[i], average="macro", zero_division=0))
    return {
        "f1_macro": f1_score(y, p, average="macro", zero_division=0),
        "f1_lo": float(np.percentile(boot, 2.5)),
        "f1_hi": float(np.percentile(boot, 97.5)),
        "accuracy": accuracy_score(y, p),
        "routing_f1": f1_score(y_route, p_route, average="macro", zero_division=0),
        "pred_technical_share": float(p_route.mean()),
        "true_technical_share": float(y_route.mean()),
    }


# --------------------------------------------------------------------------- experiment


def evaluate(n_seeds: int = N_SEEDS, html: bool = True) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Calibrate, then run every scenario over `n_seeds` seeds. Returns (runs, column scores, context)."""
    t0 = time.perf_counter()
    clf = TicketClassifier.load(Path("models"))
    test, pool_de = load_inputs()
    described_test = describe(test, clf)  # descriptors do not depend on the split: compute once
    thresholds, null = calibrate(described_test, test)
    logger.info("calibrated on %d A/A splits (%.0f s): %s", len(null), time.perf_counter() - t0, thresholds)

    runs, cols = [], []
    BUILD.mkdir(parents=True, exist_ok=True)
    for seed in range(SEED, SEED + n_seeds):
        ref = described_test.iloc[split_indices(test, seed)[0]].reset_index(drop=True)
        _, batches = build_scenarios(test, pool_de, seed)
        for name, batch in batches.items():
            cur = describe(batch, clf)
            snapshot = run_report(ref, cur, thresholds)
            res = parse(snapshot)
            default = {c: ColumnDrift(cd.method, DEFAULT_THRESHOLD, cd.score).drifted for c, cd in res.columns.items()}
            n_default = sum(default[c] for c in SERVING)
            runs.append(
                {
                    "seed": seed,
                    "scenario": name,
                    "n_ref": len(ref),
                    "n_cur": len(cur),
                    "dataset_drift": res.dataset_drift,
                    "n_drifted": len(res.drifted_serving),
                    "label_drift": res.label_drift,
                    "label_score": res.columns[TARGET].score,
                    "dataset_drift_default": n_default / len(SERVING) >= DRIFT_SHARE,
                    "n_drifted_default": n_default,
                    "label_drift_default": default[TARGET],
                    **quality(cur, clf.technical, np.random.default_rng([seed, 99])),
                }
            )
            for col, cd in res.columns.items():
                cols.append(
                    {
                        "seed": seed,
                        "scenario": name,
                        "column": col,
                        "score": cd.score,
                        "drifted": cd.drifted,
                        "drifted_default": default[col],
                    }
                )
            if html and seed == SEED:
                snapshot.save_html(str(BUILD / f"drift_{name}.html"))
        logger.info("seed %d done (%.0f s)", seed, time.perf_counter() - t0)

    ref42 = described_test.iloc[split_indices(test, SEED)[0]].reset_index(drop=True)
    context = {
        "reference_quality": quality(ref42, clf.technical, np.random.default_rng([SEED, 98])),
        "model_version": clf.model_version,
        "seconds": time.perf_counter() - t0,
        "methods": {c: cd.method for c, cd in res.columns.items()},
        "thresholds": thresholds,
        "null": null,
        "german_share": float(pool_de["text"].map(is_german).mean()),
        "pool_size": len(pool_de),
    }
    return pd.DataFrame(runs), pd.DataFrame(cols), context


# --------------------------------------------------------------------------- summary


def _f(x: float, nd: int = 3) -> str:
    return f"{x:.{nd}f}"


def _table(header: list[str]) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]


def summarize(runs: pd.DataFrame, cols: pd.DataFrame, ctx: dict[str, Any], n_seeds: int) -> str:
    scenarios = list(dict.fromkeys(runs["scenario"]))
    main = runs[runs["seed"] == SEED].set_index("scenario")
    rq, thr, null = ctx["reference_quality"], ctx["thresholds"], ctx["null"]
    ctrl_f1 = main.loc["control", "f1_macro"]
    lines = [
        "# Drift report — summary",
        "",
        f"Generated by `uv run python -m support_copilot.drift.report` · model `{ctx['model_version']}` · "
        f"Evidently 0.7.23 · calibration seeds {CALIB_SEEDS.start}–{CALIB_SEEDS.stop - 1} · "
        f"evaluation seeds {SEED}–{SEED + n_seeds - 1}.",
        "",
        "**Setup.** Reference and current batches are the two halves of the held-out test set, split by "
        "paraphrase family (no family on both sides). Each scenario transforms the current half "
        f"(seed {SEED}: reference {int(main['n_ref'].iloc[0])} tickets, batches {int(main['n_cur'].iloc[0])}). "
        f"Serving columns: {', '.join(f'`{c}`' for c in SERVING)}. Label `{TARGET}` checked apart (it arrives "
        f"late). Numeric: {ctx['methods'][NUMERIC[0]]}; categorical: {ctx['methods'][CATEGORICAL[0]]}. "
        f"Dataset drift when ≥ {DRIFT_SHARE:.0%} of the {len(SERVING)} serving columns drift.",
        "",
        f"The `language_de` scenario draws only from tickets detected as German: "
        f"{ctx['german_share']:.1%} of the {ctx['pool_size']:,} rows tagged `de` (the rest is English or undecided).",
        "",
        f"Reference half quality (seed {SEED}): F1 macro {_f(rq['f1_macro'])} [{_f(rq['f1_lo'])}, "
        f"{_f(rq['f1_hi'])}] · accuracy {_f(rq['accuracy'])} · routing F1 {_f(rq['routing_f1'])}.",
        "",
        "## Thresholds: calibrated on clean-vs-clean splits",
        "",
        f"{len(null)} A/A splits of the test set (by family), scores of each column. Calibrated threshold = "
        f"max({DEFAULT_THRESHOLD}, p{int(CALIB_QUANTILE * 100)} of the A/A scores).",
        "",
        *_table(["column", "A/A median", "A/A p95", "A/A max", f"A/A ≥ {DEFAULT_THRESHOLD}", "threshold used"]),
    ]
    for c in MONITORED:
        lines.append(
            f"| `{c}` | {_f(null[c].median())} | {_f(null[c].quantile(0.95))} | {_f(null[c].max())} "
            f"| {(null[c] >= DEFAULT_THRESHOLD).mean():.0%} | **{_f(thr[c])}** |"
        )
    lines += [
        "",
        f"## Seed {SEED} (the published HTML, calibrated thresholds)",
        "",
        *_table(
            [
                "scenario",
                "dataset drift",
                "serving columns drifted",
                "label drift (JS)",
                "F1 macro [95% bootstrap]",
                "ΔF1 vs control",
                "accuracy",
                "routing F1",
                "predicted → RAG",
            ]
        ),
    ]
    for s in scenarios:
        r = main.loc[s]
        lines.append(
            f"| {s} | {'**yes**' if r['dataset_drift'] else 'no'} | {int(r['n_drifted'])}/{len(SERVING)} "
            f"| {_f(r['label_score'])}{' ✱' if r['label_drift'] else ''} "
            f"| {_f(r['f1_macro'])} [{_f(r['f1_lo'])}, {_f(r['f1_hi'])}] | {r['f1_macro'] - ctrl_f1:+.3f} "
            f"| {_f(r['accuracy'])} | {_f(r['routing_f1'])} | {r['pred_technical_share']:.1%} |"
        )
    lines += ["", "✱ = drift detected on that column.", "", f"### Column scores, seed {SEED}", ""]
    c42 = cols[cols["seed"] == SEED]
    lines += _table(["scenario", *[f"`{c}`" for c in MONITORED]])
    for s in scenarios:
        sub = c42[c42["scenario"] == s].set_index("column")
        cells = [f"{_f(sub.loc[c, 'score'])}{' ✱' if sub.loc[c, 'drifted'] else ''}" for c in MONITORED]
        lines.append(f"| {s} | " + " | ".join(cells) + " |")
    lines += [
        "",
        f"## Over {n_seeds} seeds",
        "",
        "Control = A/A test: every alarm there is a false alarm. Mean ± standard deviation across seeds; "
        "ΔF1 is paired by seed (same reference, same current half).",
        "",
        *_table(
            [
                "scenario",
                "dataset drift (calibrated)",
                "dataset drift (default 0.1)",
                "columns flagged, calibrated / default (mean)",
                "label drift",
                "F1 macro",
                "ΔF1 vs control",
                "accuracy",
                "routing F1",
            ]
        ),
    ]
    ctrl = runs[runs["scenario"] == "control"].set_index("seed")
    for s in scenarios:
        sub = runs[runs["scenario"] == s].set_index("seed")
        delta = sub["f1_macro"] - ctrl["f1_macro"]
        lines.append(
            f"| {s} | {int(sub['dataset_drift'].sum())}/{n_seeds} "
            f"| {int(sub['dataset_drift_default'].sum())}/{n_seeds} "
            f"| {sub['n_drifted'].mean():.1f} / {sub['n_drifted_default'].mean():.1f} "
            f"| {int(sub['label_drift'].sum())}/{n_seeds} "
            f"| {_f(sub['f1_macro'].mean())} ± {_f(sub['f1_macro'].std())} "
            f"| {delta.mean():+.3f} ± {_f(delta.std())} | {_f(sub['accuracy'].mean())} "
            f"| {_f(sub['routing_f1'].mean())} ± {_f(sub['routing_f1'].std())} |"
        )
    for key, title in (("drifted", "calibrated thresholds"), ("drifted_default", "default threshold 0.1")):
        rate = cols.groupby(["scenario", "column"], sort=False)[key].mean().unstack()[MONITORED]
        lines += ["", f"Share of seeds in which each column drifted — {title}:", ""]
        lines += _table(["scenario", *[f"`{c}`" for c in MONITORED]])
        for s in scenarios:
            lines.append(f"| {s} | " + " | ".join(f"{rate.loc[s, c]:.0%}" for c in MONITORED) + " |")
    lines += [
        "",
        f"Published: {', '.join(f'`reports/{k}` ({v}, seed {SEED})' for k, v in PUBLISH.items())}. "
        "Read with `docs/findings.md` §8.",
        "",
    ]
    return "\n".join(lines)


def publish() -> None:
    for name, scenario in PUBLISH.items():
        src = BUILD / f"drift_{scenario}.html"
        shutil.copyfile(src, Path("reports") / name)
        logger.info("published %s -> reports/%s (%.1f MB)", src, name, src.stat().st_size / 1e6)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Evidently drift report for the ticket classifier")
    parser.add_argument("--seeds", type=int, default=N_SEEDS)
    parser.add_argument("--publish", action="store_true", help="copy the seed-42 HTMLs to reports/")
    args = parser.parse_args()
    runs, cols, ctx = evaluate(args.seeds)
    runs.to_csv(BUILD / "drift_runs.csv", index=False)
    cols.to_csv(BUILD / "drift_columns.csv", index=False)
    THRESHOLDS.write_text(json.dumps(ctx["thresholds"], indent=2) + "\n", encoding="utf-8", newline="\n")
    SUMMARY.write_text(summarize(runs, cols, ctx, args.seeds), encoding="utf-8", newline="\n")
    logger.info("summary written to %s (%.0f s)", SUMMARY, ctx["seconds"])
    if args.publish:
        publish()


if __name__ == "__main__":
    main()
