"""Windows checkout hazards, checked from any OS (D11).

A clone that works on macOS can still break on Windows: two paths that differ only in case collapse
into one file, reserved names (CON, NUL...) cannot be created, and paths past MAX_PATH (260) fail
unless long paths are enabled. These tests read the tracked file list, so they catch the problem in
CI before a Windows user does.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# Room left for the clone location: C:\Users\<name>\projects\support-copilot\ is ~40 characters.
MAX_TRACKED_PATH = 120
WINDOWS_RESERVED = re.compile(r"(^|/)(con|prn|aux|nul|com[1-9]|lpt[1-9])(\.[^/]*)?$", re.IGNORECASE)
WINDOWS_FORBIDDEN_CHARS = re.compile(r'[<>:"|?*\\]')


def _git(*args: str) -> list[str]:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("needs a git checkout")
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True)
    return [line for line in out.stdout.splitlines() if line]


def test_no_paths_that_collide_on_a_case_insensitive_filesystem():
    counts = Counter(path.lower() for path in _git("ls-files"))
    assert [path for path, n in counts.items() if n > 1] == []


def test_no_windows_reserved_names_or_forbidden_characters():
    paths = _git("ls-files")
    bad = [p for p in paths if WINDOWS_RESERVED.search(p) or WINDOWS_FORBIDDEN_CHARS.search(p)]
    assert bad == []


def test_tracked_paths_leave_room_under_max_path():
    longest = max(_git("ls-files"), key=len)
    assert len(longest) <= MAX_TRACKED_PATH, longest


def test_text_files_are_stored_with_lf():
    """`git ls-files --eol` reports the index line ending; CRLF there would follow every clone."""
    crlf = [line for line in _git("ls-files", "--eol") if line.split()[0] in {"i/crlf", "i/mixed"}]
    assert crlf == []
