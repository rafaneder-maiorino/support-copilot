"""Check every external link in the README and docs the way an evaluator sees it: logged out.

Requests carry no cookies, tokens or auth headers, so a private repository or an unlisted video that
needs a login shows up as broken. Local addresses (localhost, 127.0.0.1) and placeholders are skipped.

Run: uv run python -m support_copilot.links   (writes reports/_build/links_check.md; exit 1 if any link fails)
"""

from __future__ import annotations

import argparse
import logging
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
URL = re.compile(r"https?://[^\s)<>\]`'\"|]+")
TRAILING = ".,;:!?*"
SKIP_HOSTS = ("localhost", "127.0.0.1")
PLACEHOLDER = ("PENDENTE", "<", "{", "example.com")
# GitHub answers 503 intermittently to anonymous automated requests on blob pages (23/11: the same
# URL gave 503 and 200 within seconds, HEAD more often than GET). The check identifies as a browser,
# still without cookies or login, and retries transient statuses before calling a link broken.
TRANSIENT = {429, 500, 502, 503, 504}
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}

Fetch = Callable[[str, str], tuple[int, str]]


@dataclass
class LinkResult:
    url: str
    status: int | None
    ok: bool
    final_url: str = ""
    error: str = ""
    sources: list[str] = field(default_factory=list)


def default_docs(root: Path = ROOT) -> list[Path]:
    """README plus every Markdown file under docs/ (the files a reviewer reads)."""
    return [root / "README.md", *sorted((root / "docs").glob("*.md"))]


def extract_external_links(text: str) -> list[str]:
    """Unique http(s) URLs in order of appearance, without local addresses or placeholders."""
    seen: dict[str, None] = {}
    for raw in URL.findall(text):
        url = raw.rstrip(TRAILING)
        host = url.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0]
        if host in SKIP_HOSTS or any(p in url for p in PLACEHOLDER):
            continue
        seen.setdefault(url, None)
    return list(seen)


def urllib_fetch(url: str, method: str, timeout: float = 20.0) -> tuple[int, str]:
    """Anonymous request: no cookie jar, no credentials. Returns (status, final URL after redirects)."""
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"only http(s) links are checked: {url}")
    req = urllib.request.Request(url, method=method, headers=BROWSER_HEADERS)  # noqa: S310 (scheme checked above)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (scheme checked above)
            return resp.status, resp.geturl()
    except urllib.error.HTTPError as exc:
        return exc.code, url


def check_url(
    url: str, fetch: Fetch = urllib_fetch, retries: int = 3, sleep: Callable[[float], None] = time.sleep
) -> LinkResult:
    """HEAD first; GET when the server refuses HEAD (many do), retrying transient statuses with backoff.

    2xx/3xx after redirects counts as reachable, unless the redirect lands on a login page.
    """
    try:
        status, final = fetch(url, "HEAD")
        attempt = 0
        while status >= 400 and attempt <= retries:
            if attempt:
                sleep(1.5 * attempt)
            status, final = fetch(url, "GET")
            if status not in TRANSIENT:
                break
            attempt += 1
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return LinkResult(url, None, False, error=type(exc).__name__)
    ok = 200 <= status < 400 and "/login" not in final
    return LinkResult(url, status, ok, final_url=final)


def check_docs(
    docs: Iterable[Path],
    fetch: Fetch = urllib_fetch,
    root: Path = ROOT,
    sleep: Callable[[float], None] = time.sleep,
) -> list[LinkResult]:
    """Check each unique URL once and remember which files cite it."""
    sources: dict[str, list[str]] = {}
    for md in docs:
        for url in extract_external_links(md.read_text(encoding="utf-8")):
            sources.setdefault(url, []).append(str(md.relative_to(root)))
    results = []
    for url, files in sources.items():
        res = check_url(url, fetch, sleep=sleep)
        res.sources = files
        logger.info("%s %s %s", "OK " if res.ok else "BAD", res.status or res.error, url)
        results.append(res)
    return results


def render(results: list[LinkResult]) -> str:
    ok = sum(r.ok for r in results)
    lines = [
        "# Links externos, conferidos sem login",
        "",
        f"**{ok}/{len(results)}** links respondem sem login. Gerado por `uv run python -m support_copilot.links`.",
        "",
        "| status | link | citado em |",
        "|---|---|---|",
    ]
    for r in sorted(results, key=lambda r: (r.ok, r.url)):
        status = str(r.status) if r.status is not None else r.error
        mark = "ok" if r.ok else "**falhou**"
        lines.append(f"| {mark} ({status}) | {r.url} | {', '.join(r.sources)} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "reports" / "_build" / "links_check.md")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    results = check_docs(default_docs())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(results), encoding="utf-8")
    bad = [r for r in results if not r.ok]
    logger.info("%d/%d links reachable logged out; report: %s", len(results) - len(bad), len(results), args.out)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
