"""The raw dataset is trusted only if its SHA-256 matches the pinned one (offline: urlopen is faked)."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from support_copilot.data import source

PAYLOAD = b"subject,body,queue\nVPN down,cannot connect,Technical Support\n"


def _fake_urlopen(payload: bytes):
    def opener(url: str, timeout: int) -> io.BytesIO:
        return io.BytesIO(payload)

    return opener


def test_download_is_accepted_when_the_hash_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(source, "RAW_SHA256", hashlib.sha256(PAYLOAD).hexdigest())
    monkeypatch.setattr(source, "urlopen", _fake_urlopen(PAYLOAD))
    path = source.ensure_raw(tmp_path)
    assert path.read_bytes() == PAYLOAD


def test_download_with_a_different_hash_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(source, "RAW_SHA256", hashlib.sha256(PAYLOAD).hexdigest())
    monkeypatch.setattr(source, "urlopen", _fake_urlopen(PAYLOAD + b"tampered\n"))
    with pytest.raises(RuntimeError, match="hash"):
        source.ensure_raw(tmp_path)


def test_verified_local_copy_is_not_downloaded_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(source, "RAW_SHA256", hashlib.sha256(PAYLOAD).hexdigest())
    (tmp_path / source.RAW_FILE).write_bytes(PAYLOAD)

    def no_network(url: str, timeout: int) -> io.BytesIO:
        raise AssertionError("must not download a verified file")

    monkeypatch.setattr(source, "urlopen", no_network)
    assert source.ensure_raw(tmp_path).read_bytes() == PAYLOAD
