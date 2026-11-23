"""docs/requisitos.md is the final review against the challenge: every cited piece of evidence must exist."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs" / "requisitos.md"
STATUSES = ("ok", "parcial", "pendente")
FILE_EXT = {"py", "md", "html", "yaml", "yml", "json", "pdf", "joblib", "toml", "lock", "txt", "png", "jpg", "example"}
PATHLIKE = re.compile(r"^[\w.\-]+(/[\w.\-]+)*$")


def rows() -> list[list[str]]:
    lines = MATRIX.read_text(encoding="utf-8").splitlines()
    return [[c.strip() for c in ln.strip("|").split("|")] for ln in lines if re.match(r"^\| R\d\d \|", ln)]


def code_spans(cell: str) -> list[str]:
    return re.findall(r"`([^`]+)`", cell)


def test_all_27_requirements_in_order_with_a_known_status() -> None:
    table = rows()
    assert [r[0] for r in table] == [f"R{i:02d}" for i in range(1, 28)]
    for r in table:
        assert len(r) == 5, r[0]
        assert r[4].split(" ")[0] in STATUSES, r[0]
        if not r[4].startswith("ok"):
            assert "falta:" in r[4], f"{r[0]} must say what is missing"


def test_header_counts_match_the_table() -> None:
    counts = {s: sum(r[4].split(" ")[0] == s for r in rows()) for s in STATUSES}
    header = f"{counts['ok']} ok · {counts['parcial']} parcial · {counts['pendente']} pendente"
    assert header in MATRIX.read_text(encoding="utf-8")


def test_cited_files_modules_and_make_targets_exist() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    missing = []
    for r in rows():
        for span in code_spans(r[2]):
            if span.startswith("make "):
                if not re.search(rf"^{re.escape(span[5:])}:", makefile, re.M):
                    missing.append(span)
            elif PATHLIKE.match(span) and ("/" in span or span.rsplit(".", 1)[-1] in FILE_EXT):
                if not (ROOT / span).exists():
                    missing.append(span)
            elif PATHLIKE.match(span) and span.startswith("support_copilot."):
                if importlib.util.find_spec(span) is None:
                    missing.append(span)
    assert not missing, missing


def test_every_requirement_has_a_real_test_as_proof() -> None:
    defined = {
        m.group(1)
        for f in (ROOT / "tests").glob("test_*.py")
        for m in re.finditer(r"^def (test_\w+)", f.read_text(encoding="utf-8"), re.M)
    }
    for r in rows():
        cited = [s for s in code_spans(r[3]) if s.startswith("test_")]
        assert cited, f"{r[0]} has no automated proof"
        assert set(cited) <= defined, f"{r[0]} cites unknown tests: {sorted(set(cited) - defined)}"


def test_delivery_doc_links_the_repository_and_a_video_slot() -> None:
    text = (ROOT / "docs" / "entrega.md").read_text(encoding="utf-8")
    assert "https://github.com/rafaneder-maiorino/support-copilot" in text
    assert re.search(r"https://(youtu\.be|www\.youtube\.com)/\S+", text), "video link (or its placeholder) missing"
