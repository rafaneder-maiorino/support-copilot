"""The logged-out link check runs offline here: the network is replaced by a fake fetch."""

from __future__ import annotations

import urllib.error
from pathlib import Path

from support_copilot import links


def test_extracts_markdown_and_bare_urls_once_without_trailing_punctuation() -> None:
    text = (
        "See [repo](https://github.com/o/r). Clone: `git clone https://github.com/o/r.git`, then\n"
        "visit https://docs.example.org/page, and again [repo](https://github.com/o/r)."
    )
    assert links.extract_external_links(text) == [
        "https://github.com/o/r",
        "https://github.com/o/r.git",
        "https://docs.example.org/page",
    ]


def test_local_addresses_and_placeholders_are_skipped() -> None:
    text = (
        "http://localhost:8000/docs http://127.0.0.1:5001 https://youtu.be/PENDENTE "
        "https://maria.silva@example.com https://ok.org"
    )
    assert links.extract_external_links(text) == ["https://ok.org"]


def test_head_refused_falls_back_to_get() -> None:
    calls: list[str] = []

    def fetch(url: str, method: str) -> tuple[int, str]:
        calls.append(method)
        return (405, url) if method == "HEAD" else (200, url)

    res = links.check_url("https://a.org", fetch)
    assert res.ok and res.status == 200 and calls == ["HEAD", "GET"]


def test_not_found_login_redirect_and_network_error_fail() -> None:
    assert not links.check_url("https://a.org", lambda u, m: (404, u)).ok
    assert not links.check_url("https://github.com/o/private", lambda u, m: (200, "https://github.com/login")).ok

    def offline(url: str, method: str) -> tuple[int, str]:
        raise urllib.error.URLError("no route")

    res = links.check_url("https://a.org", offline)
    assert not res.ok and res.status is None and res.error == "URLError"


def test_check_docs_dedupes_across_files_and_renders_failures_first(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("https://good.org and https://bad.org", encoding="utf-8")
    (tmp_path / "b.md").write_text("again https://good.org", encoding="utf-8")
    seen: list[str] = []

    def fetch(url: str, method: str) -> tuple[int, str]:
        seen.append(url)
        return (200 if "good" in url else 500), url

    results = links.check_docs([tmp_path / "a.md", tmp_path / "b.md"], fetch, root=tmp_path, sleep=lambda s: None)
    assert seen.count("https://good.org") == 1
    good = next(r for r in results if r.ok)
    assert good.sources == ["a.md", "b.md"]
    report = links.render(results)
    assert "**1/2**" in report
    assert report.index("bad.org") < report.index("good.org")


def test_default_docs_are_the_readme_and_docs_folder() -> None:
    docs = links.default_docs()
    assert docs[0].name == "README.md"
    assert all(p.suffix == ".md" for p in docs) and len(docs) > 5


def test_transient_503_is_retried_with_backoff_before_failing() -> None:
    answers = iter([(503, "u"), (503, "u"), (200, "u")])
    waits: list[float] = []
    res = links.check_url("https://github.com/o/r/blob/main/x", lambda u, m: next(answers), sleep=waits.append)
    assert res.ok and waits == [1.5]

    waits.clear()
    res = links.check_url("https://a.org", lambda u, m: (503, u), retries=2, sleep=waits.append)
    assert not res.ok and res.status == 503 and waits == [1.5, 3.0]
