"""The requests shown in the pitch video must keep producing what the script says they produce."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from support_copilot.api.main import app

ROOT = Path(__file__).resolve().parents[1]
SCENES = json.loads((ROOT / "docs" / "demo" / "scenes.json").read_text(encoding="utf-8"))["scenes"]


def lookup(body: Any, dotted: str) -> Any:
    """Follow a dotted path such as ``answer.sources.0.section`` through dicts and lists."""
    node = body
    for part in dotted.split("."):
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_scene_ids_are_unique_and_every_scene_has_a_block() -> None:
    ids = [s["id"] for s in SCENES]
    assert len(ids) == len(set(ids))
    assert all(s["block"] in {"A1", "A2", "A3", "A4"} for s in SCENES)


@pytest.mark.parametrize("scene", SCENES, ids=lambda s: s["id"])
def test_scene_behaves_as_the_script_says(client, scene: dict[str, Any]) -> None:
    r = client.post(scene["endpoint"], json=scene["body"])
    assert r.status_code == scene["expect"]["status"], r.text
    body = r.json()
    for path, expected in scene["expect"]["fields"].items():
        assert lookup(body, path) == expected, f"{scene['id']}: {path}"


def test_lookup_walks_dicts_and_lists() -> None:
    assert lookup({"a": [{"b": 1}]}, "a.0.b") == 1
