"""Drift scenarios (pure, seeded) and the Evidently verdicts on small synthetic batches."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from support_copilot.api.classifier import TicketClassifier
from support_copilot.data.source import MAX_CHARS, QUEUES, TECHNICAL_QUEUES
from support_copilot.drift import report as rep
from support_copilot.drift import simulate as sim

EN = "My laptop does not connect to the office network after the update, please help"
DE = "Mein Laptop verbindet sich nach dem Update nicht mit dem Netzwerk, bitte helfen Sie mir"


def tickets(n: int = 400, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    labels = rng.choice(QUEUES, size=n)
    return pd.DataFrame(
        {
            "text_id": [f"{i:016d}" for i in range(n)],
            "text": [f"{EN} ticket {i} " + "word " * int(rng.integers(0, 30)) for i in range(n)],
            "label": labels,
            "is_technical": np.isin(labels, list(TECHNICAL_QUEUES)),
            "group_id": rng.integers(0, n // 3, size=n),
        }
    )


def test_reference_and_current_share_no_paraphrase_family():
    df = tickets()
    ref, cur = sim.split_reference(df, seed=1)
    assert set(ref["group_id"]).isdisjoint(cur["group_id"])
    assert len(ref) + len(cur) == len(df)


def test_scenarios_are_reproducible_with_the_same_seed():
    df, pool = tickets(), tickets(seed=1).assign(text=DE)
    _, a = sim.build_scenarios(df, pool, seed=7)
    _, b = sim.build_scenarios(df, pool, seed=7)
    for name in a:
        pd.testing.assert_frame_equal(a[name], b[name])
    _, c = sim.build_scenarios(df, pool, seed=8)
    assert not a["text_noise"]["text"].equals(c["text_noise"]["text"])


def test_control_is_the_current_half_untouched():
    df, pool = tickets(), tickets(seed=1).assign(text=DE)
    _, batches = sim.build_scenarios(df, pool, seed=3)
    _, cur = sim.split_reference(df, seed=3)
    pd.testing.assert_frame_equal(batches["control"], cur)


def test_category_mix_reaches_the_target_shares():
    df = tickets(3000)
    out = sim.category_mix(df, np.random.default_rng(0))
    share = out["label"].value_counts(normalize=True)
    assert len(out) == len(df)
    assert share["Billing and Payments"] == pytest.approx(0.40, abs=0.03)
    assert share["Customer Service"] == pytest.approx(0.15, abs=0.03)


def test_text_noise_degrades_but_keeps_the_api_contract():
    rng = np.random.default_rng(0)
    long_text = (EN + " ") * 12
    outs = [sim.degrade_text(long_text[:MAX_CHARS].strip(), rng) for _ in range(200)]
    assert all(1 <= len(o) <= MAX_CHARS and o == o.strip() for o in outs)
    assert sum(o != long_text[:MAX_CHARS].strip() for o in outs) == 200
    assert sum(len(o) < 0.5 * len(long_text[:MAX_CHARS]) for o in outs) > 50  # about half are truncated


def test_german_detection_and_language_scenario_skip_english_rows():
    assert sim.is_german(DE) and not sim.is_german(EN)
    pool = pd.concat([tickets(300, 1).assign(text=DE), tickets(300, 2).assign(text=EN)], ignore_index=True)
    out = sim.language_de(tickets(200), pool, np.random.default_rng(0))
    assert len(out) == 200 and out["text"].map(sim.is_german).all()


def test_signature_never_breaks_the_1000_character_limit():
    df = tickets(50).assign(text=["x" * 990] * 25 + ["short ticket"] * 25)
    out = sim.signature(df, np.random.default_rng(0))
    assert out["text"].str.len().max() <= MAX_CHARS
    assert (out["text"].iloc[:25] == "x" * 990).all() and out["text"].iloc[25:].str.contains("@").all()


# ----------------------------------------------------------------------------- descriptors (real model)


@pytest.fixture(scope="module")
def clf() -> TicketClassifier:
    return TicketClassifier.load(Path("models"))


def test_descriptors_separate_english_from_german(clf):
    d = rep.describe(pd.DataFrame({"text": [EN, DE], "label": ["IT Support"] * 2, "is_technical": [True] * 2}), clf)
    assert list(d.columns[: len(rep.NUMERIC)]) == rep.NUMERIC
    en, de = d.iloc[0], d.iloc[1]
    assert en["oov_share"] < 0.1 < de["oov_share"]
    assert en["non_ascii_share"] == 0 and de["non_ascii_share"] == 0  # the sample DE sentence has no umlauts
    assert set(d["pred_route"]) <= {"rag", "queue"} and d["confidence"].between(0, 1).all()


def test_descriptors_never_keep_the_raw_text(clf):
    d = rep.describe(
        pd.DataFrame({"text": [EN + " joao@example.com"], "label": ["IT Support"], "is_technical": [True]}), clf
    )
    assert "text" not in d.columns
    assert not d.astype(str).apply(lambda col: col.str.contains("example.com")).any().any()


# ----------------------------------------------------------------------------- Evidently verdicts


def described(n: int, seed: int, shift: float = 0.0, mix: float = 0.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    p = np.array([0.4, 0.3, 0.2, 0.1])
    p = (1 - mix) * p + mix * np.array([0.0, 0.0, 0.0, 1.0])
    queues = rng.choice(QUEUES[:4], size=n, p=p)
    return pd.DataFrame(
        {
            "n_chars": rng.normal(400 + 300 * shift, 150, n),
            "n_words": rng.normal(70 + 50 * shift, 25, n),
            "oov_share": rng.beta(1, 200, n) + 0.5 * shift,
            "non_letter_share": rng.beta(5, 60, n) + 0.1 * shift,
            "non_ascii_share": rng.beta(1, 500, n) + 0.05 * shift,
            "confidence": rng.beta(4, 6, n) - 0.1 * shift,
            "pred_queue": queues,
            "pred_route": np.where(np.isin(queues, list(TECHNICAL_QUEUES)), "rag", "queue"),
            "label": queues,
        }
    )


def test_evidently_flags_a_shifted_batch_and_not_a_clean_one():
    ref = described(1500, 0)
    clean = rep.check_batch(ref, described(1500, 1))
    shifted = rep.check_batch(ref, described(1500, 2, shift=1.0, mix=0.5))
    assert not clean.dataset_drift and clean.drifted_serving == [] and not clean.label_drift
    assert shifted.dataset_drift and len(shifted.drifted_serving) >= 6 and shifted.label_drift
    assert shifted.columns["n_chars"].method.lower().startswith("wasserstein")
    assert shifted.columns["pred_queue"].method.lower().startswith("jensen")


def test_calibrated_thresholds_are_used_by_evidently():
    ref, cur = described(1500, 0), described(1500, 2, shift=0.15)
    loose = {c: 1000.0 for c in rep.MONITORED}
    assert rep.check_batch(ref, cur).columns["n_chars"].drifted
    res = rep.check_batch(ref, cur, loose)
    assert res.columns["n_chars"].threshold == 1000.0 and not res.columns["n_chars"].drifted
    assert res.drifted_serving == [] and not res.dataset_drift


def test_p_value_methods_drift_below_the_threshold():
    assert rep.ColumnDrift("K-S p_value", 0.05, 0.01).drifted
    assert not rep.ColumnDrift("K-S p_value", 0.05, 0.2).drifted
    assert rep.ColumnDrift("Wasserstein distance (normed)", 0.1, 0.1).drifted
