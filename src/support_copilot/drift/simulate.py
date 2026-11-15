"""Seeded drift scenarios for the ticket classifier.

Reference and current batches both come from the held-out test set, never from train: on train the
model is overconfident, so a train reference would make the confidence column drift by construction.
The test set is cut in two halves BY PARAPHRASE FAMILY (group_id, docs/findings.md §1): the reference
half and the current half share no family, exactly like the train/test split.

Scenarios (each one changes one thing on purpose; the rest of the batch is left alone):
    control        the current half as is: same distribution, no drift expected (negative control)
    category_mix   label shift: a billing incident floods the queue (P(y) changes, P(x|y) does not)
    text_noise     channel degradation: typos, truncated messages and symbol noise (P(x|y) changes)
    language_de    covariate shift: tickets arrive in German (realistic, from the held-out German pool)
    signature      a closing line with contact data at the end of each ticket (findings §7: it flips routes)

Run: uv run python -m support_copilot.drift.simulate  -> data/processed/drift/<scenario>.parquet
"""

from __future__ import annotations

import logging
import re
import string
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupShuffleSplit

from support_copilot.data.source import MAX_CHARS, QUEUES

logger = logging.getLogger(__name__)

SEED = 42
PROCESSED = Path("data/processed")
OUT_DIR = PROCESSED / "drift"

# Billing incident: billing, customer service and returns take over; the other queues keep their
# relative weights inside the remaining 35%.
INCIDENT_MIX = {"Billing and Payments": 0.40, "Customer Service": 0.15, "Returns and Exchanges": 0.10}

TYPO_RATE = 0.20  # share of words with one typo
TRUNCATE_SHARE = 0.50  # share of tickets cut short
TRUNCATE_KEEP = 0.40  # fraction of characters kept when truncated
NOISE_RATE = 0.10  # symbol tokens inserted per word

STOP_EN = frozenset("the and is to of with for my i we our have has please this that are not you it in on".split())
STOP_DE = frozenset(
    "der die das und ist nicht mit ich wir sie für ein eine zu den bei auf von uns bitte es dem".split()
)

FIRST = ["maria", "joao", "ana", "pedro", "lucas", "julia", "carlos", "fernanda", "rafael", "beatriz"]
LAST = ["silva", "souza", "oliveira", "santos", "lima", "costa", "pereira", "almeida"]


def split_indices(test: pd.DataFrame, seed: int = SEED) -> tuple[np.ndarray, np.ndarray]:
    """Row positions of a 50/50 split of the test set by paraphrase family: (reference, current)."""
    gss = GroupShuffleSplit(n_splits=1, test_size=0.5, random_state=seed)
    ref_idx, cur_idx = next(gss.split(test, groups=test["group_id"]))
    return ref_idx, cur_idx


def split_reference(test: pd.DataFrame, seed: int = SEED) -> tuple[pd.DataFrame, pd.DataFrame]:
    """50/50 split of the test set by paraphrase family: (reference, current)."""
    ref_idx, cur_idx = split_indices(test, seed)
    return test.iloc[ref_idx].reset_index(drop=True), test.iloc[cur_idx].reset_index(drop=True)


def category_mix(current: pd.DataFrame, rng: np.random.Generator, mix: dict[str, float] | None = None) -> pd.DataFrame:
    """Resample the batch (same size, with replacement) so the queue shares follow `mix`."""
    mix = INCIDENT_MIX if mix is None else mix
    base = current["label"].value_counts(normalize=True)
    rest = [q for q in QUEUES if q not in mix and q in base.index]
    left = 1.0 - sum(mix.values())
    target = {**mix, **{q: left * base[q] / base[rest].sum() for q in rest}}
    weights = current["label"].map(target) / current["label"].map(current["label"].value_counts())
    idx = rng.choice(len(current), size=len(current), replace=True, p=(weights / weights.sum()).to_numpy())
    return current.iloc[idx].reset_index(drop=True)


def _typo(word: str, rng: np.random.Generator) -> str:
    if len(word) < 3:
        return word
    i = int(rng.integers(1, len(word) - 1))
    kind = int(rng.integers(3))
    if kind == 0:  # swap two neighbours
        return word[: i - 1] + word[i] + word[i - 1] + word[i + 1 :]
    if kind == 1:  # drop a letter
        return word[:i] + word[i + 1 :]
    return word[:i] + str(rng.choice(list(string.ascii_lowercase))) + word[i:]  # extra letter


def degrade_text(text: str, rng: np.random.Generator) -> str:
    """Typos, symbol noise and (for some tickets) truncation. Never empty, never over the API limit."""
    words = []
    for w in text.split(" "):
        words.append(_typo(w, rng) if rng.random() < TYPO_RATE else w)
        if rng.random() < NOISE_RATE:
            words.append(str(rng.choice(["#", "@@", "%", "&", "~~", "**", "|"])))
    out = " ".join(words)
    if rng.random() < TRUNCATE_SHARE:
        out = out[: max(20, int(len(out) * TRUNCATE_KEEP))]
    out = out.strip()[:MAX_CHARS].strip()
    return out or text


def text_noise(current: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    out = current.copy()
    out["text"] = [degrade_text(t, rng) for t in out["text"]]
    return out


def is_german(text: str) -> bool:
    """Stop-word vote. The dataset's `language` tag is not reliable: 27.3% of the rows tagged `de` are
    written in English (vs 0.0-0.01% of the `en` rows written in German), see docs/findings.md §8."""
    words = re.findall(r"[a-zäöüß]+", text.lower())
    return sum(w in STOP_DE for w in words) > sum(w in STOP_EN for w in words)


def language_de(current: pd.DataFrame, pool_de: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Same batch size, drawn without replacement from the German pool (never used in training),
    keeping only the tickets actually written in German."""
    german = pool_de[pool_de["text"].map(is_german)]
    idx = rng.choice(len(german), size=len(current), replace=False)
    return german.iloc[idx].reset_index(drop=True)


def contact_line(rng: np.random.Generator) -> str:
    f, la = str(rng.choice(FIRST)), str(rng.choice(LAST))
    phone = f"(19) 9{rng.integers(1000, 9999)}-{rng.integers(1000, 9999)}"
    return [
        f"\nContact: {f}.{la}@example.com",
        f"\nBest regards, {f.title()} {la.title()} - {f}.{la}@example.com - {phone}",
        f"\nYou can reach me at {f}{rng.integers(10, 99)}@gmail.com",
    ][int(rng.integers(3))]


def signature(current: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Append a contact line; skipped when it would break the 1000-character API contract."""
    out = current.copy()
    texts = []
    for t in out["text"]:
        line = contact_line(rng)
        texts.append(t + line if len(t) + len(line) <= MAX_CHARS else t)
    out["text"] = texts
    return out


def build_scenarios(
    test: pd.DataFrame, pool_de: pd.DataFrame, seed: int = SEED
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """Return (reference, {scenario: current batch}). Every scenario has its own seeded generator."""
    reference, current = split_reference(test, seed)
    makers: dict[str, Callable[[np.random.Generator], pd.DataFrame]] = {
        "control": lambda rng: current.copy(),
        "category_mix": lambda rng: category_mix(current, rng),
        "text_noise": lambda rng: text_noise(current, rng),
        "language_de": lambda rng: language_de(current, pool_de, rng),
        "signature": lambda rng: signature(current, rng),
    }
    batches = {name: make(np.random.default_rng([seed, i])) for i, (name, make) in enumerate(makers.items())}
    return reference, batches


def load_inputs(processed: Path = PROCESSED) -> tuple[pd.DataFrame, pd.DataFrame]:
    test_path, pool_path = processed / "test.parquet", processed / "drift_pool_de.parquet"
    if not test_path.exists() or not pool_path.exists():
        raise FileNotFoundError(f"{processed}/ is missing test/drift_pool_de parquet files — run `make prepare`")
    return pd.read_parquet(test_path), pd.read_parquet(pool_path)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    test, pool_de = load_inputs()
    reference, batches = build_scenarios(test, pool_de)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    reference.to_parquet(OUT_DIR / "reference.parquet", index=False)
    for name, batch in batches.items():
        batch.to_parquet(OUT_DIR / f"{name}.parquet", index=False)
        logger.info(
            "%s: %d rows, technical share %.3f, median chars %d",
            name,
            len(batch),
            batch["is_technical"].mean(),
            int(batch["text"].str.len().median()),
        )
    logger.info("reference: %d rows -> %s", len(reference), OUT_DIR)


if __name__ == "__main__":
    main()
