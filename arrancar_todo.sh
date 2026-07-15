#!/usr/bin/env bash
# arrancar_todo.sh — Lanza la API y la GUI cada una en su propia ventana de
# tmux (Linux / Raspberry Pi), equivalente a arrancar_todo.ps1 en Windows.
#   bash arrancar_todo.sh
#
# Pensado para un servidor headless por SSH: cada ventana de tmux se puede
# cerrar de forma independiente sin afectar a la otra, y los procesos siguen
# vivos aunque se cierre la conexión SSH (a diferencia de dos ventanas
# graficas, que necesitarian un servidor X).
#
# Dentro de la sesion:
#   Ctrl+b luego 0/1   cambia entre la ventana "api" y "gui"
#   Ctrl+b luego w     lista todas las ventanas
#   Ctrl+b luego d     se desconecta sin matar nada (los procesos siguen)
#   Ctrl+C y luego Ctrl+b x   cierra SOLO la ventana actual (confirma con "y")
#
# Para volver a conectarte mas tarde: tmux attach -t andalucia

set -e
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
SESSION="andalucia"

if ! command -v tmux >/dev/null 2>&1; then
    echo "[ERROR] tmux no esta instalado. Instalalo con: sudo apt install tmux"
    exit 1
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Ya existe una sesion tmux '$SESSION' en marcha. Conectando..."
    exec tmux attach -t "$SESSION"
fi

echo "Creando sesion tmux '$SESSION' con dos ventanas: api y gui..."
tmux new-session -d -s "$SESSION" -n api -c "$PROJECT_ROOT" "bash start_api.sh"
tmux new-window  -t "$SESSION" -n gui -c "$PROJECT_ROOT" "bash start_gui.sh"

echo ""
echo "Sesion '$SESSION' creada con 2 ventanas: 'api' (puerto 8000) y 'gui' (puerto 7860)."
echo "Cierra una ventana concreta (Ctrl+C, luego Ctrl+b x) para detener solo ese proceso."
echo ""
exec tmux attach -t "$SESSION"
