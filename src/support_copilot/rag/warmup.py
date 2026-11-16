"""Download the embedding model and build the FAISS index ahead of time.

Used at ``docker build`` time so the container starts offline and fast: the image already holds the
ONNX embedding model and the index for the default settings. Run locally with:

    uv run python -m support_copilot.rag.warmup
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from langchain_core.embeddings import Embeddings

from support_copilot.rag.config import RagSettings
from support_copilot.rag.ingest import load_or_build

logger = logging.getLogger("support_copilot.rag.warmup")


def warm(settings: RagSettings | None = None, emb: Embeddings | None = None) -> dict[str, object]:
    """Build (or load) the index for ``settings`` and return a small summary of what is on disk."""
    settings = settings or RagSettings()
    t0 = time.perf_counter()
    store = load_or_build(settings, emb)
    index_path = settings.index_dir / settings.cache_key()
    return {
        "chunks": int(store.index.ntotal),
        "index_dir": str(index_path),
        "index_bytes": _dir_bytes(index_path),
        "model_cache_bytes": _dir_bytes(settings.model_cache_dir),
        "seconds": round(time.perf_counter() - t0, 1),
    }


def _dir_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logger.info("warmup %s", json.dumps(warm()))


if __name__ == "__main__":
    main()
