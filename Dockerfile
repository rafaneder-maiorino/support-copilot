# syntax=docker/dockerfile:1
# Support Copilot API: triage (/predict), RAG with guardrails (/chat) and orchestration (/ticket).
#   docker build -t support-copilot .
#   docker run --rm -p 8000:8000 support-copilot        # Swagger at http://localhost:8000/docs
# The LLM key, when used, comes only from the runtime environment (.env via compose); never baked in.

ARG PYTHON_IMAGE=python:3.11-slim-bookworm

# ---------------------------------------------------------------- builder
FROM ${PYTHON_IMAGE} AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /app

# 1) Dependencies: exactly the versions in uv.lock, exported to requirements.txt (cached layer).
#    --no-deps: the file is already the full closure, so pip must not re-resolve it. Without it pip
#    follows xgboost's own metadata and pulls NVIDIA NCCL back in (~417 MB), ignoring the lock.
#    The pip cache lives in a BuildKit cache mount: faster rebuilds, never inside the image.
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install --no-deps -r requirements.txt

# 2) The package itself, without touching the pinned dependencies.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-deps . && rm -rf src

# 3) Embedding model + FAISS index for the default settings, so the container runs offline.
COPY data/manuals ./data/manuals
RUN python -m support_copilot.rag.warmup

# ---------------------------------------------------------------- runtime
FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.title="support-copilot" \
      org.opencontainers.image.source="https://github.com/rafaneder-maiorino/tech-challenge-fase5"
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_OFFLINE=1 \
    LLM_PROVIDER=fake
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin appuser
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY --from=builder --chown=appuser:appuser /app/data ./data
COPY --chown=appuser:appuser models ./models
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status != 200)"]
CMD ["uvicorn", "support_copilot.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
