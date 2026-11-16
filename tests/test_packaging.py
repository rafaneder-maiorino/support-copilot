"""Packaging contract (D9): requirements.txt mirrors uv.lock, and the image/compose keep their promises.

These are static checks so they run in a second; `docker compose up` itself is exercised in CI."""

import re
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from langchain_core.embeddings import DeterministicFakeEmbedding

from support_copilot.rag.config import RagSettings
from support_copilot.rag.warmup import warm

ROOT = Path(__file__).resolve().parents[1]
EXPORT_CMD = "uv export --frozen --no-hashes --format requirements-txt --no-dev --no-emit-project"
PIN = re.compile(r"^([A-Za-z0-9_.\-]+)==([^\s;]+)")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


@pytest.fixture(scope="module")
def pins() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        m = PIN.match(line)
        if m:
            out.setdefault(_norm(m.group(1)), []).append(m.group(2))
    return out


@pytest.fixture(scope="module")
def lock() -> dict[str, set[str]]:
    data = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    out: dict[str, set[str]] = {}
    for pkg in data["package"]:
        out.setdefault(_norm(pkg["name"]), set()).add(pkg["version"])
    return out


# ----------------------------------------------------------------------------- requirements.txt


def test_requirements_header_records_the_export_command():
    head = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()[:2]
    assert head[1].strip("# ").strip() == EXPORT_CMD


def test_every_pin_matches_uv_lock(pins, lock):
    assert len(pins) > 100
    wrong = {n: v for n, vs in pins.items() for v in vs if v not in lock.get(n, set())}
    assert wrong == {}


def test_runtime_deps_are_pinned_and_dev_deps_are_not(pins):
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    runtime = {_norm(re.split(r"[<>=\[ ]", d, maxsplit=1)[0]) for d in project["project"]["dependencies"]}
    assert runtime - pins.keys() == set()
    assert {"pytest", "ruff", "reportlab"} & pins.keys() == set()


def test_requirements_do_not_install_the_project_itself():
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    reqs = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    assert not [ln for ln in reqs if ln.startswith("-e") or "support-copilot" in ln or ln.startswith(".")]


# ----------------------------------------------------------------------------- image


def test_dockerfile_installs_from_requirements_and_drops_root():
    df = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "pip install --no-deps -r requirements.txt" in df  # the file is the closure; no re-resolve
    assert "pip install --no-deps ." in df
    users = re.findall(r"^USER\s+(\S+)", df, flags=re.M)
    assert users and users[-1] not in {"root", "0"}
    assert re.search(r"^HEALTHCHECK\s", df, flags=re.M)
    assert re.search(r"LLM_PROVIDER=fake", df) and re.search(r"HF_HUB_OFFLINE=1", df)


def test_no_secret_can_reach_the_image():
    df = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert not re.search(r"API_KEY", df)
    assert not re.search(r"^COPY\s+\.\s", df, flags=re.M), "COPY . would bypass the narrow copy list"
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    for entry in (".env", ".git", "data/raw", "data/processed", "mlflow.db", "mlartifacts", "logs"):
        assert entry in ignored


def test_compose_runs_api_and_mlflow_on_the_agreed_ports():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    api, mlflow = compose["services"]["api"], compose["services"]["mlflow"]
    assert api["ports"] == ["8000:8000"]
    assert mlflow["ports"] == ["5001:5001"]
    assert "5000" not in yaml.safe_dump(compose)
    assert api["env_file"] == [{"path": ".env", "required": False}]
    assert "environment" not in api, "env vars set here would override the provider chosen in .env"
    assert "--allowed-hosts" in mlflow["command"]
    assert "API_KEY" not in yaml.safe_dump(compose)


# ----------------------------------------------------------------------------- warmup (build step)


def test_warmup_builds_the_index_once_then_reuses_it(tmp_path):
    settings = replace(RagSettings(), index_dir=tmp_path / "index", model_cache_dir=tmp_path / "models")
    emb = DeterministicFakeEmbedding(size=16)
    first = warm(settings, emb)
    assert first["chunks"] > 100
    assert first["index_bytes"] > 0
    assert Path(first["index_dir"]).name == settings.cache_key()
    second = warm(settings, emb)
    assert second["chunks"] == first["chunks"]
