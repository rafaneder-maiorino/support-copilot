"""The README and docs are deliverables: links must resolve and the facts they state must hold."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
LINK = re.compile(r"\]\(([^)\s]+)\)")


def relative_links(md: Path) -> list[str]:
    targets = []
    for target in LINK.findall(md.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        targets.append(target.split("#", 1)[0])
    return targets


@pytest.mark.parametrize("md", DOCS, ids=lambda p: p.name)
def test_relative_links_resolve(md: Path) -> None:
    missing = [t for t in relative_links(md) if t and not (md.parent / t).exists()]
    assert not missing, f"{md.name} links to missing files: {missing}"


def test_readme_has_the_sections_the_challenge_asks_for() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for heading in ("## Caminho do avaliador", "## Arquitetura de nuvem", "## Privacy by Design e LGPD"):
        assert heading in readme
    assert "[IMPLEMENTADO]" in readme and "[PRESCRITO]" in readme
    assert readme.count("```mermaid") >= 2


def test_docs_point_to_the_renamed_repository_and_mlflow_port() -> None:
    for md in DOCS:
        text = md.read_text(encoding="utf-8")
        assert "rafaneder-maiorino/tech-challenge-fase5" not in text, md.name
        assert "localhost:5000" not in text, md.name


def test_cited_test_names_exist() -> None:
    """Every `test_*` name the privacy docs cite as evidence must be a real test."""
    defined = {
        m.group(1)
        for f in (ROOT / "tests").glob("test_*.py")
        for m in re.finditer(r"^def (test_\w+)", f.read_text(encoding="utf-8"), re.M)
    }
    for md in (ROOT / "README.md", ROOT / "docs" / "lgpd.md"):
        cited = set(re.findall(r"\b(test_[a-z0-9_]+)\b(?!\.py)", md.read_text(encoding="utf-8")))
        assert cited, md.name
        assert cited <= defined, f"{md.name} cites unknown tests: {sorted(cited - defined)}"
