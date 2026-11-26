"""Delivery PDF builder: runs offline, with headless Chrome replaced by a reportlab writer."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from support_copilot import delivery

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "docs" / "entrega.md").read_text(encoding="utf-8")
VIDEO = "https://youtu.be/AbCdEfGhIjK"
FAKE_RM = "12345"


def _pdf(path: Path, text: str, uris: list[str]) -> None:
    c = canvas.Canvas(str(path))
    c.drawString(72, 800, text)
    for i, uri in enumerate(uris):
        c.linkURL(uri, (72, 700 - 20 * i, 300, 715 - 20 * i), relative=0)
    c.save()


@pytest.mark.parametrize(
    "url",
    [
        "https://youtu.be/AbCdEfGhIjK",
        "https://youtu.be/AbCdEfGhIjK?si=share-token_1",
        "https://www.youtube.com/watch?v=AbCdEfGhIjK",
        "https://youtube.com/watch?v=AbCdEfGhIjK&t=10s",
    ],
)
def test_video_url_forms_become_one_canonical_link(url: str) -> None:
    assert delivery.canonical_video_url(url) == VIDEO


@pytest.mark.parametrize(
    "url",
    [
        "https://youtu.be/PENDENTE",
        "http://youtu.be/AbCdEfGhIjK",
        "https://vimeo.com/AbCdEfGhIjK",
        "https://youtu.be/short",
        "https://youtu.be/AbCdEfGhIjK/../x",
    ],
)
def test_bad_or_placeholder_video_url_is_refused(url: str) -> None:
    with pytest.raises(ValueError):
        delivery.canonical_video_url(url)


@pytest.mark.parametrize("rm", ["", "12a45", "1234", "12345678"])
def test_rm_must_be_digits(rm: str) -> None:
    with pytest.raises(ValueError):
        delivery.validate_rm(rm)


def test_source_in_repository_is_not_ready_and_carries_no_rm() -> None:
    assert "placeholder PENDENTE still present" in delivery.problems(SOURCE)
    assert "**Aluno:**" not in SOURCE and not re.search(r"\bRM\s*\d", SOURCE)


def test_fill_makes_the_document_ready() -> None:
    filled = delivery.fill(SOURCE, VIDEO + "?si=x", FAKE_RM)
    assert delivery.problems(filled) == []
    assert f"<{VIDEO}>" in filled and "PENDENTE" not in filled
    assert f"**Aluno:** {delivery.STUDENT} · RM {FAKE_RM}" in filled
    assert delivery.META_PARAGRAPH not in filled
    assert "](../" not in filled and f"]({delivery.REPO_BLOB}README.md)" in filled


def test_urls_inside_code_spans_stay_literal() -> None:
    filled = delivery.fill(SOURCE, VIDEO, FAKE_RM)
    assert "`git clone https://github.com/rafaneder-maiorino/support-copilot.git && cd support-copilot`" in filled
    assert "<http://localhost" not in filled
    assert "https://github.com/rafaneder-maiorino/support-copilot.git" not in delivery.external_links(filled)


def test_every_relative_link_of_the_source_points_at_a_file_in_the_repository() -> None:
    paths = delivery.RELATIVE_LINK.findall(SOURCE)
    assert paths, "expected links to repository files"
    assert [p for p in paths if not (ROOT / p).exists()] == []


def test_html_has_tables_and_clickable_absolute_links() -> None:
    page = delivery.render_html(delivery.fill(SOURCE, VIDEO, FAKE_RM))
    assert "<table>" in page and f'<a href="{VIDEO}">' in page
    assert 'href="../' not in page and "<title>Entrega — Datathon Fase 5</title>" in page
    # Projeto / Curso / Aluno one per line; wrapped source lines elsewhere do not become breaks.
    assert page.count("<br />") == 2


def test_print_pdf_calls_headless_chrome_on_the_local_file(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    html_file, pdf_file = tmp_path / "e.html", tmp_path / "e.pdf"
    html_file.write_text("<p>x</p>", encoding="utf-8")
    delivery.print_pdf(html_file, pdf_file, "chrome", run=lambda cmd, **_: calls.append(cmd))
    (cmd,) = calls
    assert cmd[0] == "chrome" and "--headless=new" in cmd and "--no-pdf-header-footer" in cmd
    assert f"--print-to-pdf={pdf_file}" in cmd and cmd[-1].startswith("file://")


def test_check_pdf_reports_missing_links_and_video(tmp_path: Path) -> None:
    pdf = tmp_path / "x.pdf"
    _pdf(pdf, "Video: https://youtu.be/AbCdEfGhIjK", ["https://a.org"])
    res = delivery.check_pdf(pdf, VIDEO, ["https://a.org", "https://b.org"])
    assert res.pages == 1 and res.video_in_text and res.missing_links == ["https://b.org"] and not res.ok


def test_build_end_to_end_with_fake_chrome(tmp_path: Path) -> None:
    out = tmp_path / "entrega.pdf"
    expected = delivery.external_links(delivery.fill(SOURCE, VIDEO, FAKE_RM))

    def fake_chrome(cmd: list[str], **_: object) -> None:
        html_text = Path(cmd[-1].removeprefix("file://")).read_text(encoding="utf-8")
        uris = re.findall(r'href="([^"]+)"', html_text)
        _pdf(Path(cmd[-2].split("=", 1)[1]), VIDEO, uris)

    res = delivery.build(VIDEO, FAKE_RM, out=out, chrome="chrome", run=fake_chrome)
    assert res.ok, res
    assert set(expected) <= set(res.uris) and VIDEO in expected
    assert out.with_suffix(".md").exists() and out.with_suffix(".html").exists()


def test_build_refuses_a_source_that_is_still_not_ready(tmp_path: Path) -> None:
    src = tmp_path / "e.md"
    src.write_text("# E\n\n[x](./local.md)\n", encoding="utf-8")
    with pytest.raises(ValueError, match="relative link"):
        delivery.build(VIDEO, FAKE_RM, source=src, out=tmp_path / "e.pdf", run=lambda *a, **k: None)


def test_main_fails_cleanly_without_chrome(tmp_path: Path) -> None:
    rc = delivery.main(
        ["--video-url", VIDEO, "--rm", FAKE_RM, "--chrome", str(tmp_path / "none"), "--out", str(tmp_path / "e.pdf")]
    )
    assert rc == 1
