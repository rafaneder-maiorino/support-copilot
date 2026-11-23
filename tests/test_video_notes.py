"""The video script must fit in five minutes and only narrate scenes that exist."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTES = ROOT / "docs" / "video-notes.md"
WORDS_PER_MINUTE = 150  # conservative narration pace in Portuguese
TOTAL_LIMIT_S = 280  # 4:40 of speech leaves 20 s for clicks within the 5:00 cap
BLOCK = re.compile(r"^### (\w+) · (\d):(\d\d)–(\d):(\d\d)", re.M)


def spoken_blocks(text: str) -> dict[str, tuple[int, int]]:
    """Return {block: (spoken words, window in seconds)} from the teleprompter section."""
    script = text.split("## Roteiro (teleprompter)", 1)[1]
    heads = list(BLOCK.finditer(script))
    out: dict[str, tuple[int, int]] = {}
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(script)
        body = script[h.end() : end]
        spoken = " ".join(line[1:] for line in body.splitlines() if line.startswith(">"))
        window = (int(h[4]) * 60 + int(h[5])) - (int(h[2]) * 60 + int(h[3]))
        out[h[1]] = (len(spoken.split()), window)
    return out


def seconds(words: int) -> float:
    return words / WORDS_PER_MINUTE * 60


def test_script_follows_star_and_ends_by_five_minutes() -> None:
    blocks = spoken_blocks(NOTES.read_text(encoding="utf-8"))
    assert list(blocks) == ["S", "T", "A1", "A2", "A3", "A4", "R"]
    assert sum(window for _, window in blocks.values()) == 300


def test_every_block_fits_its_window_and_the_total_leaves_room_for_clicks() -> None:
    blocks = spoken_blocks(NOTES.read_text(encoding="utf-8"))
    for name, (words, window) in blocks.items():
        assert seconds(words) <= window, f"{name}: {words} words = {seconds(words):.0f} s > {window} s"
    total = sum(words for words, _ in blocks.values())
    assert seconds(total) <= TOTAL_LIMIT_S, f"{total} words = {seconds(total):.0f} s"


def test_header_states_the_measured_word_count() -> None:
    text = NOTES.read_text(encoding="utf-8")
    total = sum(words for words, _ in spoken_blocks(text).values())
    assert f"**{total} palavras" in text


def test_scenes_named_in_the_script_exist() -> None:
    text = NOTES.read_text(encoding="utf-8")
    scenes = json.loads((ROOT / "docs" / "demo" / "scenes.json").read_text(encoding="utf-8"))["scenes"]
    known = {s["id"] for s in scenes}
    table = set(re.findall(r"^\| `([a-z0-9-]+)` \|", text, re.M))
    assert table == known, f"scene table and scenes.json differ: {sorted(table ^ known)}"
    script = text.split("## Roteiro (teleprompter)", 1)[1]
    named = set(re.findall(r"`((?:predict|ticket|chat)-[a-z0-9-]+)`", script))
    assert named and named <= known, sorted(named - known)
