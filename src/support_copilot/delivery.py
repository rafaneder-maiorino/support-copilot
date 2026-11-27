"""Build the delivery PDF submitted on the FIAP platform from docs/entrega.md.

The Markdown in the repository keeps the video link as a placeholder and never carries the student
ID (RM). On submission day this module fills both, turns repository-relative links into absolute
GitHub URLs (a relative link is dead inside a PDF), refuses to build while a placeholder is left,
renders HTML and prints it with headless Chrome. The PDF lands in reports/_build/ (gitignored) and
is checked by reading it back: page count, the video id in the text and one clickable link
annotation for every external URL of the source.

Run: uv run python -m support_copilot.delivery --video-url https://youtu.be/<id> --rm <RM>
"""

from __future__ import annotations

import argparse
import html
import logging
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from markdown_it import MarkdownIt
from pypdf import PdfReader

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "docs" / "entrega.md"
OUT = ROOT / "reports" / "_build" / "entrega.pdf"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
REPO_BLOB = "https://github.com/rafaneder-maiorino/support-copilot/blob/main/"
VIDEO_PLACEHOLDER = "https://youtu.be/PENDENTE"
STUDENT = "Rafael Neder Maiorino"
META_PARAGRAPH = "Fonte do documento de entrega"

YOUTUBE = re.compile(
    r"^https://(?:youtu\.be/|(?:www\.)?youtube\.com/watch\?v=)(?P<id>[A-Za-z0-9_-]{11})(?:[?&][\w=&.%-]*)?$"
)
RM = re.compile(r"^\d{5,7}$")
RELATIVE_LINK = re.compile(r"\]\(\.\./(?P<path>[^)\s]+)\)")
BARE_URL = re.compile(r"(?<![(<])(https://[^\s|)<>`]+)")
EXTERNAL = re.compile(r"https://[^\s|)<>`]+")

Runner = Callable[..., object]

CSS = """
@page { size: A4; margin: 18mm 16mm; }
body { font-family: -apple-system, "Helvetica Neue", Arial, sans-serif; font-size: 11pt; line-height: 1.45;
       color: #1a1a1a; }
h1 { font-size: 18pt; margin: 0 0 8pt; }
h2 { font-size: 13pt; margin: 16pt 0 6pt; border-bottom: 1px solid #ccc; padding-bottom: 2pt; }
table { border-collapse: collapse; width: 100%; margin: 4pt 0 8pt; }
th, td { border: 1px solid #bbb; padding: 4pt 6pt; text-align: left; vertical-align: top; }
th { background: #f2f2f2; }
code { font-family: Menlo, Consolas, monospace; font-size: 9.5pt; }
a { color: #0b57d0; word-break: break-all; }
"""


@dataclass
class PdfCheck:
    pages: int
    uris: list[str]
    missing_links: list[str] = field(default_factory=list)
    video_in_text: bool = False

    @property
    def ok(self) -> bool:
        return self.pages > 0 and self.video_in_text and not self.missing_links


def canonical_video_url(url: str) -> str:
    """Accept youtu.be and youtube.com/watch links (share parameters allowed); return https://youtu.be/<id>."""
    m = YOUTUBE.match(url.strip())
    if not m:
        raise ValueError("video URL must be https://youtu.be/<11-char id> or https://www.youtube.com/watch?v=<id>")
    return f"https://youtu.be/{m.group('id')}"


def validate_rm(rm: str) -> str:
    rm = rm.strip()
    if not RM.match(rm):
        raise ValueError("RM must be 5 to 7 digits")
    return rm


def _outside_code(line: str, fn: Callable[[str], str]) -> str:
    """Apply fn only to the parts of a line that are not inside `code spans`."""
    parts = line.split("`")
    return "`".join(fn(p) if i % 2 == 0 else p for i, p in enumerate(parts))


def fill(markdown: str, video_url: str, rm: str, student: str = STUDENT) -> str:
    """Fill the video link and the student line, drop the note about the source, make links absolute."""
    video_url = canonical_video_url(video_url)
    rm = validate_rm(rm)
    paragraphs = [p for p in markdown.split("\n\n") if not p.startswith(META_PARAGRAPH)]
    text = "\n\n".join(paragraphs).replace(VIDEO_PLACEHOLDER, video_url)
    lines = []
    for line in text.splitlines():
        line = RELATIVE_LINK.sub(lambda m: f"]({REPO_BLOB}{m.group('path')})", line)
        line = _outside_code(line, lambda s: BARE_URL.sub(r"<\1>", s))
        lines.append(line)
        if line.startswith("**Curso:**"):
            lines.append(f"**Aluno:** {student} · RM {rm}")
    # The header lines (**Projeto:**, **Curso:**, **Aluno:**) are one paragraph in Markdown; a hard
    # break keeps one per line without turning every wrapped source line into a break.
    for i in range(len(lines) - 1):
        if lines[i].startswith("**") and lines[i + 1].startswith("**"):
            lines[i] += "\\"
    return "\n".join(lines) + "\n"


def problems(markdown: str) -> list[str]:
    """Reasons the Markdown is not ready to become the delivery PDF (empty list = ready)."""
    found = []
    if "PENDENTE" in markdown:
        found.append("placeholder PENDENTE still present")
    if RELATIVE_LINK.search(markdown) or "](./" in markdown:
        found.append("relative link left (dead inside a PDF)")
    if "**Aluno:**" not in markdown:
        found.append("student line (name and RM) missing")
    return found


def external_links(markdown: str) -> list[str]:
    """Unique https URLs outside code spans, in order (the links that must be clickable in the PDF)."""
    seen: dict[str, None] = {}
    for line in markdown.splitlines():
        for i, part in enumerate(line.split("`")):
            if i % 2 == 0:
                for url in EXTERNAL.findall(part):
                    seen.setdefault(url.rstrip(".,;:"), None)
    return list(seen)


def render_html(markdown: str) -> str:
    md = MarkdownIt("commonmark", {"html": False}).enable("table")
    title = next((ln[2:].strip() for ln in markdown.splitlines() if ln.startswith("# ")), "Entrega")
    body = md.render(markdown)
    return (
        f'<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
        f"<style>{CSS}</style></head><body>{body}</body></html>\n"
    )


def print_pdf(html_file: Path, pdf_file: Path, chrome: str = CHROME, run: Runner = subprocess.run) -> None:
    run(  # noqa: S603 - fixed binary; the HTML is generated locally from a file in the repository
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--print-to-pdf={pdf_file}",
            html_file.resolve().as_uri(),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )


def check_pdf(pdf_file: Path, video_url: str, expected_links: list[str]) -> PdfCheck:
    """Read the PDF back: pages, link annotations and the video id somewhere in the text."""
    reader = PdfReader(pdf_file)
    uris: list[str] = []
    text = ""
    for page in reader.pages:
        text += page.extract_text() or ""
        for annot in page.get("/Annots") or []:
            action = annot.get_object().get("/A")
            uri = action.get_object().get("/URI") if action else None
            if uri:
                uris.append(str(uri))
    video_id = canonical_video_url(video_url).rsplit("/", 1)[1]
    return PdfCheck(
        pages=len(reader.pages),
        uris=uris,
        missing_links=[u for u in expected_links if u not in uris],
        video_in_text=video_id in "".join(text.split()),
    )


def build(
    video_url: str,
    rm: str,
    source: Path = SOURCE,
    out: Path = OUT,
    chrome: str = CHROME,
    run: Runner = subprocess.run,
) -> PdfCheck:
    filled = fill(source.read_text(encoding="utf-8"), video_url, rm)
    issues = problems(filled)
    if issues:
        raise ValueError("delivery document not ready: " + "; ".join(issues))
    out.parent.mkdir(parents=True, exist_ok=True)
    md_file, html_file = out.with_suffix(".md"), out.with_suffix(".html")
    md_file.write_text(filled, encoding="utf-8")
    html_file.write_text(render_html(filled), encoding="utf-8")
    print_pdf(html_file, out, chrome, run)
    return check_pdf(out, video_url, external_links(filled))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--video-url", required=True, help="unlisted YouTube link of the STAR video")
    parser.add_argument("--rm", required=True, help="student ID; goes only into the PDF, never into the repository")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--chrome", default=CHROME)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not Path(args.chrome).exists():
        logger.error("Google Chrome not found at %s (use --chrome)", args.chrome)
        return 1
    try:
        result = build(args.video_url, args.rm, out=args.out, chrome=args.chrome)
    except ValueError as exc:
        logger.error("%s", exc)
        return 1
    logger.info(
        "%s: %d page(s), %d link(s); video id in text: %s; missing links: %s",
        args.out,
        result.pages,
        len(result.uris),
        result.video_in_text,
        result.missing_links or "none",
    )
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
