import re
import random
import shutil
from pathlib import Path

import gradio as gr
from config import OUTPUT_ROOT
from ui.api_client import get_clips, actualizar_transcripcion, borrar_clip_api


# ---------------------------------------------------------------------------
# Estado por sesión (gr.State): cada usuario tiene su propia copia
# ---------------------------------------------------------------------------

def _default_state() -> dict:
    return {
        "rows": [],
        "rows_filtradas": [],
        "idx": 0,
        "mun_dir": None,
        "hablante_filtro": "Todos",
        "id_map": {},
    }


def _extraer_hablante(audio_key: str) -> str:
    m = re.match(r"wavs/(\d+)_(\d{2})_", audio_key)
    return m.group(2) if m else "??"


def _aplicar_filtro(state: dict, hablante: str) -> dict:
    s = dict(state)
    todas = s["rows"]
    s["rows_filtradas"] = (
        list(todas) if not hablante or hablante == "Todos"
        else [r for r in todas if _extraer_hablante(r["nombre_archivo"]) == hablante]
    )
    s["idx"] = 0
    s["hablante_filtro"] = hablante
    return s


def _clip_actual(state: dict):
    """Devuelve las 6 salidas de UI correspondientes al clip actual."""
    rows   = state["rows_filtradas"]
    idx    = state["idx"]
    mun_dir = state["mun_dir"]
    opciones = [Path(r["nombre_archivo"]).name for r in rows]

    if not rows:
        return (
            None, "", "Sin clips.",
            gr.update(interactive=False),
            gr.update(interactive=False),
            gr.update(choices=[], value=None),
        )

    row    = rows[idx]
    nombre = row["nombre_archivo"]
    texto  = row["transcripcion"] or ""
    hab    = _extraer_hablante(nombre)
    wav    = (Path(mun_dir) / nombre) if mun_dir else None
    label  = f"Clip {idx + 1} de {len(rows)} | Hablante {hab} | {nombre}"

    return (
        str(wav) if wav and wav.exists() else None,
        texto,
        label,
        gr.update(interactive=idx > 0),
        gr.update(interactive=idx < len(rows) - 1),
        gr.update(choices=opciones, value=Path(nombre).name),
    )


# ---------------------------------------------------------------------------
# Helpers de UI
# ---------------------------------------------------------------------------

def get_municipios_procesados():
    opciones = []
    for meta in sorted(OUTPUT_ROOT.rglob("metadata.csv")):
        parts = meta.parent.name.rsplit("_", 1)
        if len(parts) == 2:
            nombre, cod = parts
            opciones.append(f"{nombre} (INE {cod})")
    return opciones


# ---------------------------------------------------------------------------
# Manejadores de eventos — todos reciben y devuelven `state`
# ---------------------------------------------------------------------------

def cargar_para_revision(municipio_choice_rev, hablante_filtro, state):
    _empty = gr.update(choices=[], value=None)
    s0 = _default_state()
    err = (
        None, "", "Selecciona un municipio.",
        gr.update(interactive=False), gr.update(interactive=False),
        _empty, gr.update(choices=["Todos"], value="Todos"), s0,
    )
    if not municipio_choice_rev:
        return err
    m = re.search(r"\(INE (\d+)\)", municipio_choice_rev)
    if not m:
        return err
    cod = m.group(1)

    posibles = [p for p in OUTPUT_ROOT.rglob(f"*_{cod}") if p.is_dir()]
    mun_dir  = str(posibles[0]) if posibles else None

    try:
        clips = get_clips(activo=True)
        rows  = [c for c in clips if c["nombre_archivo"].startswith(f"wavs/{cod}_")]
    except Exception as e:
        s0["mun_dir"] = mun_dir
        return (
            None, "", f"Error API: {e}",
            gr.update(interactive=False), gr.update(interactive=False),
            _empty, gr.update(choices=["Todos"], value="Todos"), s0,
        )

    s = _default_state()
    s["rows"]    = rows
    s["mun_dir"] = mun_dir
    s["id_map"]  = {r["nombre_archivo"]: r["id"] for r in rows}

    hablantes    = sorted({_extraer_hablante(r["nombre_archivo"]) for r in rows})
    opciones_hab = ["Todos"] + hablantes
    filtro       = hablante_filtro if hablante_filtro in opciones_hab else "Todos"
    s = _aplicar_filtro(s, filtro)

    audio, texto, progreso, btn_a, btn_sig, clip_dd = _clip_actual(s)
    return (
        audio, texto, progreso, btn_a, btn_sig, clip_dd,
        gr.update(choices=opciones_hab, value=filtro),
        s,
    )


def cambiar_filtro_hablante(hablante, state):
    s = _aplicar_filtro(state, hablante)
    return (*_clip_actual(s), s)


def saltar_a_clip(nombre_archivo, state):
    s = dict(state)
    for i, r in enumerate(s["rows_filtradas"]):
        if Path(r["nombre_archivo"]).name == nombre_archivo:
            s["idx"] = i
            break
    return (*_clip_actual(s), s)


def guardar_y_siguiente(nueva_texto, state):
    s      = dict(state)
    rows_f = s["rows_filtradas"]
    if not rows_f:
        return (*_clip_actual(s), s)

    row     = rows_f[s["idx"]]
    clip_id = s["id_map"].get(row["nombre_archivo"])
    if clip_id:
        try:
            actualizar_transcripcion(clip_id, nueva_texto.strip())
            row["transcripcion"] = nueva_texto.strip()
            for r in s["rows"]:
                if r["nombre_archivo"] == row["nombre_archivo"]:
                    r["transcripcion"] = nueva_texto.strip()
        except Exception:
            pass

    if s["idx"] < len(rows_f) - 1:
        s["idx"] += 1
    return (*_clip_actual(s), s)


def borrar_clip(state):
    s      = dict(state)
    rows_f = s["rows_filtradas"]
    idx    = s["idx"]
    mun_dir = s["mun_dir"]

    if not rows_f:
        return (*_clip_actual(s), s)

    row     = rows_f[idx]
    nombre  = row["nombre_archivo"]
    clip_id = s["id_map"].get(nombre)

    wav_path = (Path(mun_dir) / nombre) if mun_dir else None
    if wav_path and wav_path.exists():
        destino_dir = Path(mun_dir) / "_eliminados"
        destino_dir.mkdir(exist_ok=True)
        destino = destino_dir / wav_path.name
        if destino.exists():
            destino = destino_dir / f"{wav_path.stem}_{random.randint(1000, 9999)}{wav_path.suffix}"
        shutil.move(str(wav_path), str(destino))

    if clip_id:
        try:
            borrar_clip_api(clip_id)
        except Exception:
            pass

    s["rows"]           = [r for r in s["rows"] if r["nombre_archivo"] != nombre]
    s["rows_filtradas"] = [r for r in rows_f    if r["nombre_archivo"] != nombre]
    s["idx"]            = min(idx, max(0, len(s["rows_filtradas"]) - 1))
    return (*_clip_actual(s), s)


def anterior_clip(state):
    s = dict(state)
    if s["idx"] > 0:
        s["idx"] -= 1
    return (*_clip_actual(s), s)


def siguiente_clip(state):
    s = dict(state)
    if s["idx"] < len(s["rows_filtradas"]) - 1:
        s["idx"] += 1
    return (*_clip_actual(s), s)


# ---------------------------------------------------------------------------
# UI Gradio
# ---------------------------------------------------------------------------

def build_tab():
    state = gr.State(_default_state())

    gr.Markdown("### Escucha cada clip y corrige la transcripcion si es necesario")
    gr.Markdown(
        "> **Borrar clip** mueve el WAV a `_eliminados/` (recuperable). "
        "Se registra el borrado lógico en la base de datos."
    )

    with gr.Row():
        mun_rev_dd  = gr.Dropdown(choices=get_municipios_procesados(), label="Municipio a revisar", scale=3)
        btn_cargar  = gr.Button("Cargar", scale=1)
        btn_act_rev = gr.Button("Actualizar lista", scale=1)

    btn_act_rev.click(
        lambda: gr.update(choices=get_municipios_procesados()),
        outputs=mun_rev_dd,
    )

    with gr.Row():
        hab_rev_dd = gr.Dropdown(choices=["Todos"], value="Todos", label="Filtrar por hablante", scale=2)
        clip_dd    = gr.Dropdown(choices=[], value=None, label="Saltar a clip", scale=4)

    progreso_lbl = gr.Textbox(label="Progreso", interactive=False)
    audio_pl     = gr.Audio(label="Clip actual", type="filepath", interactive=False)
    transcr_box  = gr.Textbox(label="Transcripcion (edita si hay errores)", lines=3)

    with gr.Row():
        btn_ant = gr.Button("Anterior", interactive=False)
        btn_ok  = gr.Button("Guardar y siguiente", variant="primary")
        btn_del = gr.Button("Borrar clip → _eliminados/", variant="stop")
        btn_sig = gr.Button("Siguiente", interactive=False)

    # 6 salidas de UI + state
    rev_outs = [audio_pl, transcr_box, progreso_lbl, btn_ant, btn_sig, clip_dd]

    btn_cargar.click(
        cargar_para_revision,
        inputs=[mun_rev_dd, hab_rev_dd, state],
        outputs=rev_outs + [hab_rev_dd, state],
    )
    hab_rev_dd.change(
        cambiar_filtro_hablante,
        inputs=[hab_rev_dd, state],
        outputs=rev_outs + [state],
    )
    clip_dd.change(
        saltar_a_clip,
        inputs=[clip_dd, state],
        outputs=rev_outs + [state],
    )
    btn_ok.click(
        guardar_y_siguiente,
        inputs=[transcr_box, state],
        outputs=rev_outs + [state],
    )
    btn_del.click(
        borrar_clip,
        inputs=[state],
        outputs=rev_outs + [state],
    )
    btn_ant.click(
        anterior_clip,
        inputs=[state],
        outputs=rev_outs + [state],
    )
    btn_sig.click(
        siguiente_clip,
        inputs=[state],
        outputs=rev_outs + [state],
    )
