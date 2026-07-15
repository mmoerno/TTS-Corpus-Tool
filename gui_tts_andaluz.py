#!/usr/bin/env python3
"""
gui_tts_andaluz.py
Punto de entrada de la interfaz Gradio para Andalucía TTS.
"""

# ---------------------------------------------------------------------------
# IMPORTANTE: fijar HF_HOME ANTES de cualquier import que cargue
# huggingface_hub (gradio lo importa internamente al ser importado).
# huggingface_hub cachea la ruta en el momento de importarse, por lo que
# cambiarla después no tiene efecto.
# ---------------------------------------------------------------------------
import os as _os
from pathlib import Path as _Path

def _setup_hf_cache():
    """
    Fija la caché de HuggingFace a .hf_cache/ dentro del proyecto.
    Se ejecuta antes de cualquier import para que huggingface_hub la lea
    correctamente. También parchea huggingface_hub.constants si ya está
    cargado (ocurre cuando un .pth del venv lo importa antes que nosotros).
    """
    import sys as _sys

    # Leer HF_HOME del .env con stdlib puro (sin python-dotenv aún)
    _env = _Path(__file__).parent / ".env"
    if _env.exists():
        for _line in _env.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                _os.environ.setdefault(_k.strip(), _v.strip())

    # Determinar ruta destino
    _hf_env = _os.environ.get("HF_HOME", "")
    if _hf_env:
        _drive = _Path(_hf_env).drive      # "M:", "C:", "" en Linux/Mac
        _ok = (not _drive) or _Path(_drive + "\\").exists()
    else:
        _ok = False

    if _ok:
        _target = _Path(_hf_env)
    else:
        # Portable: .hf_cache/ junto al código
        _target = _Path(__file__).parent / ".hf_cache"

    _target.mkdir(parents=True, exist_ok=True)
    _hub = str(_target / "hub")

    # 1) Fijar variables de entorno (para imports futuros)
    _os.environ["HF_HOME"] = str(_target)
    _os.environ["HUGGINGFACE_HUB_CACHE"] = _hub
    _os.environ["HF_HUB_CACHE"] = _hub
    _os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

    # 2) Parchear huggingface_hub.constants si ya estaba importado
    #    (gradio lo importa via .pth antes de que corra gui_tts_andaluz.py)
    _const = _sys.modules.get("huggingface_hub.constants")
    if _const is not None:
        _const.HF_HOME = str(_target)
        if hasattr(_const, "HF_HUB_CACHE"):
            _const.HF_HUB_CACHE = _hub
        if hasattr(_const, "HUGGINGFACE_HUB_CACHE"):
            _const.HUGGINGFACE_HUB_CACHE = _hub

    print(f"[env] Cache HF: {_target}")


def _patch_symlinks_windows():
    import sys as _sys
    if _sys.platform != "win32":
        return
    import shutil as _sh
    _orig = _os.symlink
    def _safe(src, dst, target_is_directory=False, *, dir_fd=None):
        try:
            _orig(src, dst, target_is_directory=target_is_directory, dir_fd=dir_fd)
        except OSError:
            s = str(src)
            d = str(dst)
            if not _os.path.isabs(s):
                s = _os.path.normpath(_os.path.join(_os.path.dirname(d), s))
            if _os.path.isdir(s):
                if not _os.path.exists(d):
                    _sh.copytree(s, d)
            elif _os.path.isfile(s):
                _sh.copy2(s, d)
    _os.symlink = _safe


_setup_hf_cache()
_patch_symlinks_windows()
# ---------------------------------------------------------------------------

import json
import shutil
from pathlib import Path
from ui.api_client import get_clips

import gradio as gr

# BrotliMiddleware de Gradio 6.18 corrompe respuestas cuando el navegador
# cancela una petición a mitad de un stream comprimido (frecuente con el log
# en vivo del entrenamiento y el polling de estado): revienta con
# "RuntimeError: Response content shorter than Content-Length" y esa petición
# se pierde sin que la interfaz avise. No hay parámetro público en
# app.launch() para desactivarlo, así que se sustituye por un middleware
# transparente antes de construir la app.
import gradio.routes as _gradio_routes


class _NoOpMiddleware:
    def __init__(self, app, *args, **kwargs):
        self.app = app

    async def __call__(self, scope, receive, send):
        await self.app(scope, receive, send)


_gradio_routes.BrotliMiddleware = _NoOpMiddleware

# Capturar stdout/stderr en un búfer para la pestaña «Logs» en vivo. Se instala
# cuanto antes para no perder la salida del arranque (carga del NGA, etc.).
from ui import log_stream
log_stream.install()

from procesar_audios_andalucia import (
    NGA_CSV, OUTPUT_ROOT, GLOBAL_CSV,
    PROVINCIAS_DISPLAY,
)
from core.nga import cargar_nga
from ui.tab_procesar    import build_tab as build_tab_procesar
from ui.tab_revisar     import build_tab as build_tab_revisar
from ui.tab_estadisticas import build_tab as build_tab_estadisticas
from ui.tab_entrenar    import build_tab as build_tab_entrenar
from ui.tab_exportar    import build_tab as build_tab_exportar
from ui.tab_bienvenida import build_tab as build_tab_bienvenida
from ui.tab_usuarios    import build_tab as build_tab_usuarios
from ui.api_client import login as api_login, get_me as api_get_me, API_BASE
import csv
import re

print(f"[ui] API_BASE={API_BASE}")

# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------
SESSION_FILE = Path(__file__).parent / "session.json"


def _gradio_auth(uvus: str, password: str) -> bool:
    """Auth de Gradio: valida credenciales contra la API y guarda el token."""
    try:
        api_login(uvus, password)
        return True
    except Exception as exc:
        print(f"Gradio auth falló: {exc}")
        return False
def load_session() -> dict:
    if SESSION_FILE.exists():
        try:
            return json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def save_session(data: dict):
    existing = load_session()
    existing.update(data)
    SESSION_FILE.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
    )

# ---------------------------------------------------------------------------
# Banner del dataset
# ---------------------------------------------------------------------------


def get_dataset_info() -> dict:
    info = {"clips": 0, "hablantes": set(), "municipios": set()}
    try:
        clips = get_clips(activo=True)
        for c in clips:
            info["clips"] += 1
            # hablante: segundo campo del nombre 41095_01_...
            partes = Path(c["nombre_archivo"]).stem.split("_")
            if len(partes) >= 2:
                info["hablantes"].add(partes[1])
            # municipio: primer campo
            if len(partes) >= 1:
                info["municipios"].add(partes[0])
    except Exception:
        pass
    info["hablantes"] = len(info["hablantes"])
    info["municipios"] = len(info["municipios"])
    return info

def banner_dataset() -> str:
    info = get_dataset_info()
    if info["clips"] == 0:
        return "Sin datos. No se ha procesado ningun audio todavia."
    return (
        f"Dataset: **{info['clips']} clips** | "
        f"**{info['hablantes']} hablantes** | "
        f"**{info['municipios']} municipio(s)**"
    )


# ---------------------------------------------------------------------------
# Limpieza _tmp al arrancar
# ---------------------------------------------------------------------------
def cleanup_tmp_dir():
    tmp_dir = OUTPUT_ROOT / "_tmp"
    if not tmp_dir.exists():
        return 0
    removed = 0
    for p in tmp_dir.glob("metadata_*.csv"):
        try:
            p.unlink(); removed += 1
        except Exception:
            pass
    return removed

# ---------------------------------------------------------------------------
# Arranque
# ---------------------------------------------------------------------------
print("Cargando NGA...")
NGA_TOPONIMOS, MUNICIPIOS_NGA = cargar_nga(NGA_CSV)
print("NGA listo.")

_tmp_removed = cleanup_tmp_dir()
print(f"Limpieza _tmp: {_tmp_removed} CSV(s) temporales eliminados.")

PROVINCIAS_CHOICES = [
    f"{cod} - {nombre}" for cod, nombre in sorted(PROVINCIAS_DISPLAY.items())
]

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
with gr.Blocks(title="Andalucía TTS") as app:

    gr.Markdown("# Andalucía TTS")
    gr.Markdown(
        "Herramienta para construir datasets de audio para fine-tuning de XTTS v2 "
        "con los acentos provinciales de Andalucia."
    )

    with gr.Row():
        dataset_banner_md = gr.Markdown(banner_dataset())
        btn_refrescar = gr.Button("Refrescar estado", scale=0)
        gr.Button("Cerrar sesión", scale=0, link="/logout")
    btn_refrescar.click(banner_dataset, outputs=dataset_banner_md)

    with gr.Tab("Bienvenida"):
        build_tab_bienvenida()

    with gr.Tab("Procesar audios"):
        build_tab_procesar(PROVINCIAS_CHOICES, MUNICIPIOS_NGA, NGA_TOPONIMOS)

    with gr.Tab("Revisar transcripciones", visible=False) as tab_revisar:
        build_tab_revisar()

    with gr.Tab("Estadísticas"):
        row_vaciar_papelera = build_tab_estadisticas()

    with gr.Tab("Entrenar XTTS", visible=False) as tab_entrenar:
        build_tab_entrenar()

    with gr.Tab("Exportar datasets", visible=False) as tab_exportar:
        build_tab_exportar(PROVINCIAS_CHOICES, MUNICIPIOS_NGA)

    with gr.Tab("Logs"):
        gr.Markdown(
            "Salida en vivo del servidor: carga de modelos, síntesis, entrenamiento, "
            "avisos y errores. Se refresca automáticamente cada 2 segundos."
        )
        with gr.Row():
            logs_auto_chk = gr.Checkbox(value=True, label="Auto-refresco", scale=0)
            btn_logs_refrescar = gr.Button("Refrescar ahora", scale=0)
            btn_logs_limpiar   = gr.Button("Limpiar", scale=0)
        logs_box = gr.Textbox(
            label="Log del servidor", lines=28, max_lines=28,
            interactive=False, autoscroll=True, value=log_stream.get_log_text,
        )
        logs_timer = gr.Timer(2.0)
        logs_timer.tick(
            lambda activo: gr.update(value=log_stream.get_log_text()) if activo else gr.update(),
            inputs=logs_auto_chk, outputs=logs_box,
        )
        btn_logs_refrescar.click(log_stream.get_log_text, outputs=logs_box)
        btn_logs_limpiar.click(log_stream.clear, outputs=logs_box)

    with gr.Tab("Gestión de usuarios", visible=False) as tab_usuarios:
        build_tab_usuarios()

    def _mostrar_tab_usuarios_si_admin():
        """Solo visible si el usuario que acaba de iniciar sesión es admin."""
        try:
            rol = api_get_me().get("rol")
        except Exception:
            rol = None
        return gr.update(visible=(rol == "admin"))

    def _mostrar_tab_revisar_si_autorizado():
        """El audio y las transcripciones solo son accesibles a revisor/admin."""
        try:
            rol = api_get_me().get("rol")
        except Exception:
            rol = None
        return gr.update(visible=(rol in ("admin", "revisor")))

    def _mostrar_vaciar_papelera_si_admin():
        """Borrado permanente de todo el dataset: reservado a admin."""
        try:
            rol = api_get_me().get("rol")
        except Exception:
            rol = None
        return gr.update(visible=(rol == "admin"))

    def _mostrar_tab_entrenar_si_admin():
        """Entrenamiento y sintesis: coste computacional alto (horas de CPU/GPU)
        y capacidad de usar el audio de cualquier hablante como referencia;
        reservado a admin."""
        try:
            rol = api_get_me().get("rol")
        except Exception:
            rol = None
        return gr.update(visible=(rol == "admin"))

    def _mostrar_tab_exportar_si_autorizado():
        """La exportación expone las transcripciones de todos los hablantes,
        no solo las del usuario actual: reservada a revisor/admin."""
        try:
            rol = api_get_me().get("rol")
        except Exception:
            rol = None
        return gr.update(visible=(rol in ("admin", "revisor")))

    app.load(_mostrar_tab_usuarios_si_admin, outputs=tab_usuarios)
    app.load(_mostrar_tab_revisar_si_autorizado, outputs=tab_revisar)
    app.load(_mostrar_vaciar_papelera_si_admin, outputs=row_vaciar_papelera)
    app.load(_mostrar_tab_entrenar_si_admin, outputs=tab_entrenar)
    app.load(_mostrar_tab_exportar_si_autorizado, outputs=tab_exportar)

if __name__ == "__main__":
    app.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        inbrowser=True,
        auth=_gradio_auth,
        auth_message="Accede con tu UVUS y contraseña",
        allowed_paths=[str(OUTPUT_ROOT)],
        theme=gr.themes.Soft(primary_hue="orange"),
    )