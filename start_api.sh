#!/usr/bin/env bash
# start_api.sh — Lanzador de la API FastAPI (Linux / Raspberry Pi)
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
HF_CACHE="$PROJECT_ROOT/.hf_cache"

export HF_HOME="$HF_CACHE"
export HUGGINGFACE_HUB_CACHE="$HF_CACHE/hub"
export HF_HUB_CACHE="$HF_CACHE/hub"
export HF_HUB_DISABLE_SYMLINKS_WARNING=1

echo "[start_api] HF_HOME=$HF_HOME"

VENV="$PROJECT_ROOT/venv"
if [ -f "$VENV/bin/activate" ]; then
    source "$VENV/bin/activate"
fi

uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
