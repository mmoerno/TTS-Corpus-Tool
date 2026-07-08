#!/usr/bin/env bash
# start_gui.sh — Lanzador de la interfaz Gradio (Linux / Raspberry Pi)
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
HF_CACHE="$PROJECT_ROOT/.hf_cache"

export HF_HOME="$HF_CACHE"
export HUGGINGFACE_HUB_CACHE="$HF_CACHE/hub"
export HF_HUB_CACHE="$HF_CACHE/hub"
export HF_HUB_DISABLE_SYMLINKS_WARNING=1

echo "[start_gui] HF_HOME=$HF_HOME"

VENV="$PROJECT_ROOT/venv"
if [ -f "$VENV/bin/activate" ]; then
    source "$VENV/bin/activate"
fi

python "$PROJECT_ROOT/gui_tts_andaluz.py"
