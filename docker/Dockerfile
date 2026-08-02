# Per-run workspace image for Pixibot work agents (DESIGN.md §11).
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work

# Common quality tooling used by the mechanical checkpoint gate (§10).
RUN pip install --no-cache-dir pytest ruff mypy

CMD ["bash"]
