"""
ui/tab_entrenar.py
Pestaña de síntesis y entrenamiento TTS.

Modos:
  Zero-shot   — Clona cualquier voz con un clip de 6-12 s, sin entrenar nada.
                Soportado por XTTS v2 (local) y F5-TTS (servidor).
  Fine-tuning — Entrena el modelo con los datos recolectados del dataset.
                Soportado por XTTS v2 y Piper.

En ambos modos se puede seleccionar un hablante concreto del dataset.
"""
import re
import csv
import wave
import shutil
from datetime import datetime
from pathlib import Path

import gradio as gr

from config import OUTPUT_ROOT, EXPORT_ROOT
from data.export import export_dataset
from core.train import (
    get_available_models,
    get_trainer_for_model,
    XTTSTrainer,
    PiperTrainer,
)
from procesar_audios_andalucia import convert_direct

# ---------------------------------------------------------------------------
# Constantes de modo
# ---------------------------------------------------------------------------
MODO_ZS = "Zero-shot"
MODO_FT = "Fine-tuning"


# ---------------------------------------------------------------------------
# Helpers de datos
# ---------------------------------------------------------------------------

def _duracion_wav(wav_path: Path) -> float:
    try:
        with wave.open(str(wav_path), "r") as wf:
            return wf.getnframes() / wf.getframerate()
    except Exception:
        return 0.0


def get_hablantes_con_clips() -> list:
    """
    Devuelve una lista de strings 'Hablante XX — Municipio (INE YYY)'
    para todos los hablantes que tengan al menos un clip con transcripción.
    """
    opciones = []
    vistos   = set()
    for meta in sorted(OUTPUT_ROOT.rglob("metadata.csv")):
        parts = meta.parent.name.rsplit("_", 1)
        if len(parts) != 2:
            continue
        nombre, cod = parts
        try:
            with open(meta, newline="", encoding="utf-8") as f:
                for row in csv.reader(f, delimiter="|"):
                    if not row or row[0] == "audio":
                        continue
                    m = re.match(r"wavs/(\d+)_(\d{2})_", row[0])
                    if m and len(row) > 1 and row[1].strip():
                        hab = m.group(2)
                        key = f"{cod}_{hab}"
                        if key not in vistos:
                            vistos.add(key)
                            opciones.append(f"Hablante {hab} — {nombre} (INE {cod})")
        except Exception:
            pass
    return opciones


def get_fuentes_entrenamiento() -> list:
    opciones = ["Dataset global (todos los municipios)"]
    provs = sorted({
        meta.parent.parent.name
        for meta in OUTPUT_ROOT.rglob("metadata.csv")
        if meta.parent.parent.name != OUTPUT_ROOT.name
    })
    for prov in provs:
        opciones.append(f"Provincia: {prov}")
    for meta in sorted(OUTPUT_ROOT.rglob("metadata.csv")):
        parts = meta.parent.name.rsplit("_", 1)
        if len(parts) == 2:
            nombre, cod = parts
            opciones.append(f"{nombre} (INE {cod})")
            # Hablantes individuales dentro del municipio
            habs = set()
            try:
                with open(meta, newline="", encoding="utf-8") as f:
                    for row in csv.reader(f, delimiter="|"):
                        if not row or row[0] == "audio":
                            continue
                        mo = re.match(r"wavs/(\d+)_(\d{2})_", row[0])
                        if mo:
                            habs.add(mo.group(2))
            except Exception:
                pass
            for hab in sorted(habs):
                opciones.append(f"Hablante {hab} — {nombre} (INE {cod})")
    return opciones


def get_modelos_entrenados() -> list:
    """Modelos ya entrenados en exports/modelos/, detectando el tipo por lo que contiene
    cada carpeta. Permite sintetizar con un modelo de una sesión anterior sin reentrenar."""
    modelos_dir = Path(EXPORT_ROOT) / "modelos"
    if not modelos_dir.exists():
        return []
    resultado = []
    for carpeta in sorted(modelos_dir.iterdir()):
        if not carpeta.is_dir():
            continue
        onnx = carpeta / "model.onnx"
        if onnx.exists():
            resultado.append({"nombre": carpeta.name, "model_code": "piper",
                               "model_dir": str(carpeta), "onnx_path": str(onnx)})
        elif list(carpeta.rglob("config.json")):
            resultado.append({"nombre": carpeta.name, "model_code": "xtts",
                               "model_dir": str(carpeta), "onnx_path": None})
        elif list(carpeta.rglob("*.ckpt")):
            # Piper entrenado pero todavía sin exportar a ONNX.
            resultado.append({"nombre": carpeta.name, "model_code": "piper",
                               "model_dir": str(carpeta), "onnx_path": None})
    return resultado


def resolver_fuente_csv(fuente: str):
    try:
        if fuente.startswith("Dataset global"):
            from config import GLOBAL_CSV
            return GLOBAL_CSV, None
        if fuente.startswith("Provincia:"):
            prov = fuente.split("Provincia:")[-1].strip()
            p = OUTPUT_ROOT / prov / "metadata.csv"
            return (p if p.exists() else None), None
        m_hab = re.match(r"Hablante (\d{2}) — .+ \(INE (\d+)\)", fuente)
        if m_hab:
            hab, cod = m_hab.group(1), m_hab.group(2)
            for meta in OUTPUT_ROOT.rglob(f"*_{cod}/metadata.csv"):
                return meta, f"wavs/{cod}_{hab}_"
        m_ine = re.search(r"\(INE (\d+)\)$", fuente)
        if m_ine:
            cod = m_ine.group(1)
            for meta in OUTPUT_ROOT.rglob(f"*_{cod}/metadata.csv"):
                return meta, None
    except Exception:
        pass
    return None, None


def _contar_clips(csv_path: Path) -> int:
    n = 0
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.reader(f, delimiter="|"):
                if row and row[0] not in ("audio", "") and len(row) > 1 and row[1].strip():
                    n += 1
    except Exception:
        pass
    return n


def _default_ft_state() -> dict:
    return {
        "model_code": None,
        "model_dir":  None,
        "onnx_path":  None,
        "trained":    False,
    }


# ---------------------------------------------------------------------------
# Zero-shot — selección de referencia
# ---------------------------------------------------------------------------

def get_clips_de_hablante(hablante_str: str) -> list:
    """
    Devuelve lista de dicts {wav, dur, trans, key} para todos los clips
    del hablante. La transcripción se lee de la BD (corregida); si la BD
    no está disponible se usa el CSV como fallback.
    Excluye clips marcados como inactivos (borrados en revisión).
    """
    if not hablante_str:
        return []
    m = re.match(r"Hablante (\d{2}) .+ \(INE (\d+)\)", hablante_str)
    if not m:
        return []
    hab, cod = m.group(1), m.group(2)
    metas = list(OUTPUT_ROOT.rglob(f"*_{cod}/metadata.csv"))
    if not metas:
        return []
    mun_dir = metas[0].parent

    # Transcripciones corregidas y estado activo desde la BD
    trans_db  = {}
    inactivos = set()
    try:
        from data.db import get_session, Clip as ClipModel
        with get_session() as session:
            clips_bd = (
                session.query(ClipModel)
                .filter(ClipModel.nombre_archivo.like(f"wavs/{cod}_{hab}_%"))
                .all()
            )
            for c in clips_bd:
                if c.activo:
                    trans_db[c.nombre_archivo] = c.transcripcion or ""
                else:
                    inactivos.add(c.nombre_archivo)
    except Exception:
        pass

    resultados = []
    try:
        with open(metas[0], newline="", encoding="utf-8") as f:
            for row in csv.reader(f, delimiter="|"):
                if not row or row[0] == "audio":
                    continue
                key = row[0]
                if not key.startswith(f"wavs/{cod}_{hab}_") or key in inactivos:
                    continue
                wav = mun_dir / key
                if not wav.exists():
                    continue
                trans = trans_db.get(key, row[1] if len(row) > 1 else "")
                resultados.append({"wav": wav, "dur": _duracion_wav(wav),
                                   "trans": trans, "key": key})
    except Exception:
        pass
    return resultados


def _clips_choices(clips: list) -> list:
    """Lista de (etiqueta_display, ruta_wav) para gr.Dropdown."""
    choices = []
    for c in clips:
        snippet = (c["trans"][:65] + "…") if len(c["trans"]) > 65 else (c["trans"] or "—")
        label   = f"{c['wav'].name}  ·  {c['dur']:.1f}s  —  {snippet}"
        choices.append((label, str(c["wav"])))
    return choices


def actualizar_clips_hablante(hablante_str: str):
    """
    Al cambiar de hablante: rellena el dropdown de clips y preselecciona
    el mejor (5-12 s con transcripción).
    Devuelve (clip_dd_update, clip_info_md, ref_audio_auto).
    """
    clips = get_clips_de_hablante(hablante_str)
    if not clips:
        return gr.update(choices=[], value=None), "No hay clips disponibles para este hablante.", None

    choices = _clips_choices(clips)
    ideales = [c for c in clips if 5 <= c["dur"] <= 12 and c["trans"]]
    default = ideales[0] if ideales else clips[0]

    info = (
        f"**{default['wav'].name}** · {default['dur']:.1f} s  "
        f"({len(clips)} clip{'s' if len(clips) != 1 else ''} disponibles)\n\n"
        f"> {default['trans']}"
    )
    return gr.update(choices=choices, value=str(default["wav"])), info, str(default["wav"])


def mostrar_info_clip(wav_path: str, hablante_str: str):
    """Al seleccionar un clip concreto: actualiza info y previsualización."""
    if not wav_path:
        return "", None
    clips = get_clips_de_hablante(hablante_str)
    clip  = next((c for c in clips if str(c["wav"]) == wav_path), None)
    if not clip:
        return "", wav_path
    info = (
        f"**{clip['wav'].name}** · {clip['dur']:.1f} s\n\n"
        f"> {clip['trans']}"
    )
    return info, wav_path


# ---------------------------------------------------------------------------
# Zero-shot — síntesis
# ---------------------------------------------------------------------------

def _normalizar_referencia(src_path: str, dst_dir: Path) -> str:
    """
    Convierte un audio de referencia subido/grabado al mismo formato interno
    que usa 'Procesar audios' (WAV mono 22050 Hz 16 bits), igual que se hace
    con las grabaciones del corpus. Devuelve la ruta convertida, o la
    original si la conversión falla.
    """
    dst_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S%f")
    dst_path = dst_dir / f"ref_{ts}.wav"
    if convert_direct(src_path, dst_path):
        return str(dst_path)
    return src_path


def sintetizar_zero_shot(
    modelo_nombre: str,
    texto: str,
    fuente_ref: str,
    clip_seleccionado: str,
    audio_subida: str,
    audio_mic: str,
):
    """Generator: emite (msg, audio_out) mientras trabaja."""
    if fuente_ref == "Hablante del dataset":
        ref = clip_seleccionado
    else:
        ref = audio_subida or audio_mic

    if not ref:
        yield "Selecciona un hablante o sube un audio de referencia.", None
        return
    if not texto or not texto.strip():
        yield "Escribe el texto a sintetizar.", None
        return

    models     = get_available_models()
    model_code = next((c for c, i in models.items() if i["name"] == modelo_nombre), None)

    if not model_code or not models.get(model_code, {}).get("supports_zero_shot"):
        yield f"{modelo_nombre} no soporta zero-shot.", None
        return

    zs_dir = Path(EXPORT_ROOT) / "zero_shot"

    if fuente_ref != "Hablante del dataset":
        yield "Convirtiendo audio de referencia al formato interno (WAV 22050 Hz mono)…", None
        ref = _normalizar_referencia(ref, zs_dir / "ref_tmp")

    yield f"Preparando síntesis zero-shot con {modelo_nombre}…", None

    zs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_wav = zs_dir / f"zs_{model_code}_{ts}.wav"

    if model_code == "xtts":
        ok, log = XTTSTrainer.zero_shot_synthesize(texto.strip(), ref, output_wav)
    else:
        yield f"Síntesis local de {modelo_nombre} no implementada todavía.", None
        return

    msg = "\n".join(log)
    yield msg, (str(output_wav) if ok and output_wav.exists() else None)


# ---------------------------------------------------------------------------
# Fine-tuning — verificación del dataset
# ---------------------------------------------------------------------------

def verificar_dataset(fuente: str, modelo_nombre: str):
    if not fuente or not modelo_nombre:
        return "Selecciona una fuente y un modelo.", gr.update(interactive=False)

    models     = get_available_models()
    model_code = next((c for c, i in models.items() if i["name"] == modelo_nombre), None)
    if not model_code:
        return f"Modelo desconocido: {modelo_nombre}", gr.update(interactive=False)

    csv_path, _ = resolver_fuente_csv(fuente)
    if not csv_path or not csv_path.exists():
        return f"CSV no encontrado para: **{fuente}**", gr.update(interactive=False)

    n_clips = _contar_clips(csv_path)
    est_min = round((n_clips * 4.5) / 60, 1)
    min_rec = models[model_code].get("recommended_min_duration_min", 20)

    if n_clips == 0:
        estado, ok = "❌ Sin datos — procesa audios primero", False
    elif est_min >= min_rec:
        estado, ok = "✅ Suficiente para entrenamiento real", True
    elif est_min >= 5:
        estado, ok = "⚠️ Limitado — válido para demo TFG", True
    else:
        estado, ok = f"❌ Insuficiente (mínimo recomendado: {min_rec} min)", False

    msg = (
        f"**{modelo_nombre}** · {fuente.split('(')[0].strip()}\n\n"
        f"| Clips | Duración estimada | Mínimo | Estado |\n"
        f"|-------|-------------------|--------|--------|\n"
        f"| {n_clips} | ~{est_min} min | {min_rec} min | {estado} |"
    )
    return msg, gr.update(interactive=ok)


# ---------------------------------------------------------------------------
# Fine-tuning — actualización de UI según modelo
# ---------------------------------------------------------------------------

def _nombre_por_fuente(fuente: str) -> str:
    if not fuente or fuente.startswith("Dataset global"):
        return "modelo_andaluz_global_v1"
    if fuente.startswith("Provincia:"):
        p = fuente.split("Provincia:")[1].strip().lower().replace(" ", "_")
        return f"modelo_{p}_v1"
    m_hab = re.match(r"Hablante (\d{2}) — (.+) \(INE (\d+)\)", fuente)
    if m_hab:
        nombre = m_hab.group(2).lower().replace(" ", "_")
        return f"modelo_{nombre}_hab{m_hab.group(1)}_v1"
    m = re.search(r"^(.+) \(INE (\d+)\)$", fuente)
    if m:
        return f"modelo_{m.group(1).lower().replace(' ', '_')}_v1"
    return "modelo_andaluz_v1"


def actualizar_nombre_modelo(fuente):
    return gr.update(value=_nombre_por_fuente(fuente))


def actualizar_config_por_modelo(modelo_nombre: str):
    """
    Retorna updates para:
      modo_dd, xtts_group, piper_group,    ← Paso 2
      epochs_sl, batch_sl, lr_tx,          ← Paso 2
      ref_audio_ft_group, onnx_group       ← Paso 4
    """
    models     = get_available_models()
    model_code = next((c for c, i in models.items() if i["name"] == modelo_nombre), None)

    if not model_code:
        info = list(get_available_models().values())[0]
        model_code = list(get_available_models().keys())[0]
    else:
        info = models[model_code]

    opciones = []
    if info.get("supports_local"):
        opciones.append("Local")
    if info.get("supports_remote"):
        opciones.append("Servidor")
    opciones = opciones or ["Local"]

    return (
        gr.update(choices=opciones, value=opciones[0], interactive=len(opciones) > 1),
        gr.update(visible=model_code == "xtts"),
        gr.update(visible=model_code == "piper"),
        gr.update(
            value=info["epoch_default"],
            minimum=info["epoch_min"],
            maximum=info["epoch_max"],
            step=info["epoch_step"],
        ),
        gr.update(value=info["batch_default"]),
        gr.update(value=info["lr_default"]),
        gr.update(visible=model_code == "xtts"),
        gr.update(visible=model_code == "piper"),
    )


# ---------------------------------------------------------------------------
# Fine-tuning — entrenamiento
# ---------------------------------------------------------------------------

def iniciar_entrenamiento(
    fuente, modelo_nombre, modo_entreno,
    epochs, batch_size, lr, model_name,
    xtts_desde_base, xtts_checkpoint_dir,
    piper_quality,
    state,
    progress=gr.Progress(),
):
    log           = []
    current_state = dict(state)

    def emit():
        return "\n".join(log), current_state

    def L(msg):
        log.append(msg)

    if not fuente or not modelo_nombre:
        L("[ERROR] Selecciona fuente y modelo.")
        yield emit()
        return

    models     = get_available_models()
    model_code = next((c for c, i in models.items() if i["name"] == modelo_nombre), None)
    if not model_code:
        L(f"[ERROR] Modelo no reconocido: {modelo_nombre}")
        yield emit()
        return

    L(f"Modelo : {modelo_nombre}")
    L(f"Fuente : {fuente.split('(')[0].strip()}")
    L(f"Modo   : {modo_entreno}")
    L(f"Épocas : {int(epochs)}  |  Batch: {int(batch_size)}  |  LR: {lr}")
    L("─" * 55)
    yield emit()

    csv_path, hablante_prefix = resolver_fuente_csv(fuente)
    if not csv_path or not csv_path.exists():
        L(f"[ERROR] CSV no encontrado para: {fuente}")
        yield emit()
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_dir = Path(EXPORT_ROOT) / "_train_tmp" / f"{model_code}_{ts}"
    export_dir.mkdir(parents=True, exist_ok=True)
    L(f"Generando export {model_code.upper()} temporal…")
    yield emit()

    try:
        export_dataset(
            csv_path, export_dir, [model_code],
            copy_mode="copy", split="all",
            hablante_prefix=hablante_prefix,
        )
    except Exception as e:
        L(f"[ERROR] Export fallido: {e}")
        yield emit()
        return

    fmt_dir   = export_dir / model_code
    train_csv = fmt_dir / ("metadata_train.csv" if model_code == "xtts" else "metadata.csv")
    eval_csv  = fmt_dir / "metadata_eval.csv" if model_code == "xtts" else None

    if not train_csv.exists():
        L("[ERROR] CSV de entrenamiento no generado.")
        yield emit()
        return

    n_clips = _contar_clips(train_csv)
    L(f"Export listo: {n_clips} clips en {fmt_dir.name}/")
    yield emit()

    output_dir = Path(EXPORT_ROOT) / "modelos" / (model_name or _nombre_por_fuente(fuente))
    output_dir.mkdir(parents=True, exist_ok=True)

    train_config = {
        "train_csv":  str(train_csv),
        "eval_csv":   str(eval_csv) if eval_csv else None,
        "output_dir": str(output_dir),
        "epochs":     int(epochs),
        "batch_size": int(batch_size),
        "lr":         str(lr),
        "quality":    piper_quality or "medium",
    }
    if model_code == "xtts" and xtts_desde_base and xtts_checkpoint_dir:
        train_config["xtts_checkpoint_dir"] = xtts_checkpoint_dir.strip()

    try:
        trainer_class = get_trainer_for_model(model_code)
    except ValueError as e:
        L(f"[ERROR] {e}")
        yield emit()
        return

    L(f"Iniciando entrenamiento {'local' if modo_entreno == 'Local' else 'remoto'}…")
    L(f"Salida: {output_dir}")
    L("─" * 55)
    yield emit()

    if modo_entreno == "Local":
        success, train_log = trainer_class.train_local(train_config)
    else:
        success, train_log = trainer_class.train_remote(train_config, "http://127.0.0.1:8000")

    log.extend(train_log)

    if success:
        current_state.update({
            "model_code": model_code,
            "model_dir":  str(output_dir),
            "onnx_path":  None,
            "trained":    True,
        })
        L("")
        L(f"Entrenamiento completado → {output_dir}")
        if model_code == "piper":
            L("Siguiente: 'Exportar a ONNX' en el Paso 4.")
    else:
        L("")
        L("Entrenamiento finalizado con errores.")

    try:
        shutil.rmtree(export_dir, ignore_errors=True)
    except Exception:
        pass

    yield "\n".join(log), current_state


def _abrir_paso4_si_entrenado(state):
    return gr.update(open=state.get("trained", False))


# ---------------------------------------------------------------------------
# Fine-tuning — prueba del modelo entrenado
# ---------------------------------------------------------------------------

def exportar_onnx(state):
    if not state.get("trained") or state.get("model_code") != "piper":
        return "Entrena primero un modelo Piper.", None, state
    ok, log_lines, onnx_path = PiperTrainer.export_onnx(Path(state["model_dir"]))
    new_state = dict(state)
    if ok and onnx_path:
        new_state["onnx_path"] = str(onnx_path)
    return "\n".join(log_lines), None, new_state


def _estado_desde_modelo(nombre: str, state: dict):
    """Devuelve (new_state, info, msg) para un modelo entrenado por nombre, o
    (state, None, msg_error) si no se encuentra. No toca la interfaz."""
    info = next((m for m in get_modelos_entrenados() if m["nombre"] == nombre), None)
    if not info:
        return state, None, f"Modelo '{nombre}' no encontrado en exports/modelos/."
    new_state = dict(state)
    new_state.update({
        "model_code": info["model_code"],
        "model_dir":  info["model_dir"],
        "onnx_path":  info["onnx_path"],
        "trained":    True,
    })
    msg = f"Modelo '{nombre}' ({info['model_code']}) cargado desde {info['model_dir']}."
    if info["model_code"] == "piper" and not info["onnx_path"]:
        msg += " Exporta primero a ONNX (botón de abajo) antes de sintetizar."
    return new_state, info, msg


def cargar_modelo_existente(nombre: str, state: dict):
    """Carga un modelo ya entrenado (de una sesión anterior o de otra máquina) sin
    necesidad de volver a entrenar ni de mantener viva la sesión que lo entrenó."""
    if not nombre:
        return state, gr.update(), gr.update(), "Selecciona un modelo."

    new_state, info, msg = _estado_desde_modelo(nombre, state)
    print(f"[cargar_modelo] {msg}")
    if info is None:
        return state, gr.update(), gr.update(), msg
    return (
        new_state,
        gr.update(visible=info["model_code"] == "xtts"),
        gr.update(visible=info["model_code"] == "piper"),
        msg,
    )


def sintetizar_ft(texto: str, ref_file: str, ref_mic: str, state: dict, modelo_seleccionado: str = None):
    # Referencia: fichero subido (cualquier formato) o grabación de micrófono.
    ref_audio = ref_file or ref_mic
    # Generador: la síntesis XTTS en CPU tarda minutos y XTTSTrainer.synthesize()
    # es bloqueante, así que se emite feedback antes de arrancar para que el
    # cuadro de resultado no quede mudo (y no parezca que la app se ha colgado).

    # Red de seguridad: si el estado de sesión no refleja un modelo cargado
    # (p. ej. el evento .change del desplegable no llegó a propagar el estado en
    # esta sesión de Gradio) pero hay un modelo elegido en el desplegable, se
    # carga aquí mismo a partir de ese nombre. Así la síntesis no depende de que
    # el estado se haya propagado antes.
    if not state.get("trained") and modelo_seleccionado:
        state, info, msg = _estado_desde_modelo(modelo_seleccionado, state)
        print(f"[sintetizar_ft] auto-carga: {msg}")

    if not state.get("trained"):
        yield ("No hay ningún modelo cargado. Selecciona uno en «Cargar un modelo "
               "ya entrenado» o completa el entrenamiento (Paso 3).", None)
        return
    if not texto or not texto.strip():
        yield "Escribe un texto de prueba.", None
        return

    model_code = state.get("model_code")
    output_wav = Path(state["model_dir"]) / "_prueba_ft.wav"

    if model_code == "xtts":
        if not ref_audio:
            yield "XTTS v2 necesita un audio de referencia (graba o sube un clip).", None
            return
        # Convertir la referencia al mismo formato interno que el corpus
        # (WAV mono 22050 Hz 16 bits), igual que zero-shot y «Procesar audios».
        # Además del beneficio de coherencia, el cargador de audio de XTTS usa
        # soundfile (ver _patch_xtts_audio_loading), que no lee MP3/M4A: sin
        # esta conversión una referencia en esos formatos haría fallar la síntesis.
        yield "Convirtiendo audio de referencia al formato interno (WAV 22050 Hz mono)…", None
        ref_audio = _normalizar_referencia(ref_audio, Path(state["model_dir"]) / "ref_tmp")
        yield ("Sintetizando con XTTS v2... En CPU esto puede tardar varios minutos; "
               "no cierres ni recargues la pestaña.", None)
        ok, log = XTTSTrainer.synthesize(
            Path(state["model_dir"]), texto.strip(), ref_audio, output_wav
        )
    elif model_code == "piper":
        onnx = state.get("onnx_path")
        if not onnx:
            yield "Exporta primero a ONNX (botón de arriba).", None
            return
        yield "Sintetizando con Piper...", None
        ok, log = PiperTrainer.synthesize(Path(onnx), texto.strip(), output_wav)
    else:
        yield f"Síntesis no implementada para {model_code}.", None
        return

    # Volcar el log también al stdout para que quede en la pestaña «Logs».
    print("[sintetizar_ft] " + " | ".join(log))
    yield "\n".join(log), (str(output_wav) if ok and output_wav.exists() else None)


# ---------------------------------------------------------------------------
# UI — construcción del tab
# ---------------------------------------------------------------------------

def build_tab():
    ft_state = gr.State(_default_ft_state())

    gr.Markdown("## Síntesis y Entrenamiento TTS")

    # ── Selector de modo ───────────────────────────────────────────────────
    modo_radio = gr.Radio(
        choices=[MODO_ZS, MODO_FT],
        value=MODO_ZS,
        label="Modo de operación",
        info=(
            "Zero-shot: sintetiza cualquier voz con un clip de 6-12 s, sin entrenar nada.  "
            "Fine-tuning: entrena el modelo con tus datos recolectados."
        ),
    )

    # ══════════════════════════════════════════════════════════════════════
    # SECCIÓN ZERO-SHOT
    # ══════════════════════════════════════════════════════════════════════
    with gr.Group(visible=True) as zs_group:

        gr.Markdown(
            "### Zero-shot\n"
            "XTTS v2 clona la voz del hablante a partir de un clip de referencia "
            "de **6-12 segundos**. No requiere entrenamiento previo.  \n"
            "Primera ejecución: descarga el modelo base (~1.8 GB)."
        )

        # Modelo zero-shot
        zs_modelos = [
            i["name"]
            for i in get_available_models().values()
            if i.get("supports_zero_shot")
        ]
        modelo_zs_dd = gr.Dropdown(
            choices=zs_modelos,
            value=zs_modelos[0] if zs_modelos else None,
            label="Modelo",
            scale=2,
        )

        gr.Markdown("#### Audio de referencia")
        fuente_ref_radio = gr.Radio(
            choices=["Hablante del dataset", "Subir / Grabar"],
            value="Hablante del dataset",
            label="Fuente",
        )

        # — Hablante del dataset —
        with gr.Group(visible=True) as hablante_dataset_grp:
            with gr.Row():
                hablante_zs_dd = gr.Dropdown(
                    choices=get_hablantes_con_clips(),
                    label="Hablante",
                    scale=4,
                    info="Solo aparecen hablantes con clips revisados.",
                )
                btn_refresh_hab = gr.Button("🔄", scale=0, min_width=55)
            clip_zs_dd = gr.Dropdown(
                choices=[],
                value=None,
                label="Clip de referencia",
                info="Elige el clip que mejor suene. La transcripción mostrada es la corregida en Revisión.",
            )
            clip_info_md   = gr.Markdown("")
            ref_audio_auto = gr.Audio(
                label="Previsualización del clip seleccionado",
                type="filepath",
                interactive=False,
            )

        # — Subir / Grabar —
        with gr.Group(visible=False) as subir_grabar_grp:
            ref_audio_subida = gr.File(
                label="Subir audio o vídeo de referencia (6-12 segundos)",
                file_types=None,
                type="filepath",
            )
            gr.Markdown("*Acepta cualquier formato procesable por ffmpeg (igual que en Procesar audios).*")
            ref_audio_mic = gr.Audio(
                label="…o grabar directamente con el micrófono",
                type="filepath",
                sources=["microphone"],
            )

        gr.Markdown("#### Texto a sintetizar")
        texto_zs = gr.Textbox(
            value="El aceite de oliva del campo andaluz está mu rico, ¿verdad?",
            label="Texto",
            lines=2,
        )
        btn_sintetizar_zs = gr.Button("🔊 Sintetizar (zero-shot)", variant="primary")
        with gr.Row():
            msg_zs    = gr.Textbox(label="Log", interactive=False, lines=4)
            audio_zs  = gr.Audio(label="Audio generado", type="filepath", interactive=False)

    # ══════════════════════════════════════════════════════════════════════
    # SECCIÓN FINE-TUNING
    # ══════════════════════════════════════════════════════════════════════
    with gr.Group(visible=False) as ft_group:

        gr.Markdown(
            "### Fine-tuning\n"
            "Entrena el modelo con tus datos recolectados para obtener una voz "
            "personalizada con acento andaluz.\n\n"
            "| Modelo | Datos mínimos | Hardware recomendado |\n"
            "|--------|--------------|----------------------|\n"
            "| XTTS v2 | ~30 min | GPU 4 GB |\n"
            "| Piper | ~20 min | CPU o GPU |\n"
        )

        # ── Paso 1 ──────────────────────────────────────────────────────
        with gr.Accordion("📁  Paso 1 — Fuente de datos", open=True):
            with gr.Row():
                fuente_ft_dd = gr.Dropdown(
                    choices=get_fuentes_entrenamiento(),
                    value="Dataset global (todos los municipios)",
                    label="Fuente",
                    scale=4,
                    info="Puedes seleccionar todo el dataset, una provincia, un municipio o un hablante concreto.",
                )
                btn_refresh_ft = gr.Button("🔄", scale=0, min_width=55)
            with gr.Row():
                modelo_ft_dd = gr.Dropdown(
                    choices=[i["name"] for i in get_available_models().values()],
                    value=list(get_available_models().values())[0]["name"],
                    label="Modelo",
                    scale=2,
                )
                modo_dd = gr.Dropdown(
                    choices=["Local"],
                    value="Local",
                    label="Modo de ejecución",
                    scale=1,
                    interactive=False,
                )
            btn_verificar = gr.Button("🔍 Verificar dataset", variant="secondary")
            msg_verificar = gr.Markdown("")

        # ── Paso 2 ──────────────────────────────────────────────────────
        with gr.Accordion("⚙️  Paso 2 — Configuración", open=True):
            model_name_tx = gr.Textbox(
                value="modelo_andaluz_v1",
                label="Nombre del modelo (carpeta de salida en exports/modelos/)",
            )
            with gr.Row():
                epochs_sl = gr.Slider(1, 500, value=10, step=1,    label="Épocas")
                batch_sl  = gr.Slider(1, 32,  value=2,  step=1,    label="Batch size")
                lr_tx     = gr.Textbox(value="5e-6",               label="Learning rate")

            with gr.Group(visible=True) as xtts_grp:
                gr.Markdown("**Opciones XTTS v2**")
                xtts_desde_base    = gr.Checkbox(value=True, label="Fine-tune desde modelo base (recomendado)")
                xtts_checkpoint_tx = gr.Textbox(
                    label="Ruta al modelo base descargado (carpeta con config.json)",
                    placeholder="C:/modelos/XTTS-v2",
                    info="Descarga en: huggingface.co/coqui/XTTS-v2",
                )

            with gr.Group(visible=False) as piper_grp:
                gr.Markdown("**Opciones Piper**")
                piper_quality = gr.Radio(
                    ["low", "medium", "high"],
                    value="medium",
                    label="Calidad",
                    info="low = rápido · medium = equilibrado · high = mejor calidad (más lento)",
                )

        # ── Paso 3 ──────────────────────────────────────────────────────
        with gr.Accordion("🚀  Paso 3 — Entrenamiento", open=True):
            btn_train = gr.Button("▶  Iniciar entrenamiento", variant="primary", interactive=False)
            log_train = gr.Textbox(
                label="Log", lines=16, max_lines=200,
                interactive=False, autoscroll=True,
            )

        # ── Paso 4 ──────────────────────────────────────────────────────
        with gr.Accordion("🎙️  Paso 4 — Probar el modelo entrenado", open=False) as paso4_acc:
            with gr.Row():
                modelo_existente_dd = gr.Dropdown(
                    choices=[m["nombre"] for m in get_modelos_entrenados()],
                    label="Cargar un modelo ya entrenado",
                    info="De exports/modelos/, sin reentrenar ni depender de esta sesión",
                    scale=3,
                )
                btn_refrescar_modelos = gr.Button("🔄", scale=0)

            texto_ft = gr.Textbox(
                value="El aceite de oliva del campo andaluz está mu rico, ¿verdad?",
                label="Texto de prueba",
                lines=2,
            )

            with gr.Group(visible=True) as ref_audio_ft_grp:
                gr.Markdown("*XTTS v2 necesita un audio de referencia del hablante (6-12 s).*")
                # Se usa gr.File (no gr.Audio) para subir: gr.Audio filtra por
                # tipo MIME en el navegador y rechaza formatos como el .opus de
                # WhatsApp antes de llegar al servidor. gr.File acepta cualquier
                # fichero y la conversión posterior (ffmpeg) lo normaliza.
                ref_file_ft = gr.File(
                    label="Subir audio o vídeo de referencia (6-12 s) — cualquier formato",
                    file_types=None,
                    type="filepath",
                )
                ref_mic_ft = gr.Audio(
                    label="…o grabar directamente con el micrófono",
                    type="filepath",
                    sources=["microphone"],
                )

            with gr.Group(visible=False) as onnx_grp:
                gr.Markdown("*Piper necesita exportar el checkpoint a ONNX antes de sintetizar.*")
                btn_onnx = gr.Button("📦 Exportar checkpoint → ONNX", variant="secondary")

            btn_sintetizar_ft = gr.Button("🔊 Sintetizar (fine-tuned)", variant="primary")
            with gr.Row():
                msg_ft   = gr.Textbox(label="Resultado", interactive=False, lines=3)
                audio_ft = gr.Audio(label="Audio generado", type="filepath", interactive=False)

    # ── Eventos: modo ──────────────────────────────────────────────────────
    modo_radio.change(
        lambda m: (gr.update(visible=m == MODO_ZS), gr.update(visible=m == MODO_FT)),
        inputs=[modo_radio],
        outputs=[zs_group, ft_group],
    )

    # ── Eventos: zero-shot ─────────────────────────────────────────────────
    btn_refresh_hab.click(
        lambda: gr.update(choices=get_hablantes_con_clips()),
        outputs=hablante_zs_dd,
    )

    fuente_ref_radio.change(
        lambda f: (
            gr.update(visible=f == "Hablante del dataset"),
            gr.update(visible=f == "Subir / Grabar"),
        ),
        inputs=[fuente_ref_radio],
        outputs=[hablante_dataset_grp, subir_grabar_grp],
    )

    hablante_zs_dd.change(
        actualizar_clips_hablante,
        inputs=[hablante_zs_dd],
        outputs=[clip_zs_dd, clip_info_md, ref_audio_auto],
    )

    clip_zs_dd.change(
        mostrar_info_clip,
        inputs=[clip_zs_dd, hablante_zs_dd],
        outputs=[clip_info_md, ref_audio_auto],
    )

    btn_sintetizar_zs.click(
        sintetizar_zero_shot,
        inputs=[modelo_zs_dd, texto_zs, fuente_ref_radio, clip_zs_dd, ref_audio_subida, ref_audio_mic],
        outputs=[msg_zs, audio_zs],
    )

    # ── Eventos: fine-tuning ───────────────────────────────────────────────
    btn_refresh_ft.click(
        lambda: gr.update(choices=get_fuentes_entrenamiento()),
        outputs=fuente_ft_dd,
    )

    fuente_ft_dd.change(actualizar_nombre_modelo, inputs=fuente_ft_dd, outputs=model_name_tx)

    modelo_ft_dd.change(
        actualizar_config_por_modelo,
        inputs=[modelo_ft_dd],
        outputs=[
            modo_dd,
            xtts_grp, piper_grp,
            epochs_sl, batch_sl, lr_tx,
            ref_audio_ft_grp, onnx_grp,
        ],
    )

    btn_verificar.click(
        verificar_dataset,
        inputs=[fuente_ft_dd, modelo_ft_dd],
        outputs=[msg_verificar, btn_train],
    )

    btn_train.click(
        iniciar_entrenamiento,
        inputs=[
            fuente_ft_dd, modelo_ft_dd, modo_dd,
            epochs_sl, batch_sl, lr_tx, model_name_tx,
            xtts_desde_base, xtts_checkpoint_tx,
            piper_quality,
            ft_state,
        ],
        outputs=[log_train, ft_state],
    ).then(
        _abrir_paso4_si_entrenado,
        inputs=[ft_state],
        outputs=[paso4_acc],
    )

    btn_onnx.click(
        exportar_onnx,
        inputs=[ft_state],
        outputs=[msg_ft, audio_ft, ft_state],
    )

    btn_sintetizar_ft.click(
        sintetizar_ft,
        inputs=[texto_ft, ref_file_ft, ref_mic_ft, ft_state, modelo_existente_dd],
        outputs=[msg_ft, audio_ft],
    )

    btn_refrescar_modelos.click(
        lambda: gr.update(choices=[m["nombre"] for m in get_modelos_entrenados()]),
        outputs=modelo_existente_dd,
    )

    modelo_existente_dd.change(
        cargar_modelo_existente,
        inputs=[modelo_existente_dd, ft_state],
        outputs=[ft_state, ref_audio_ft_grp, onnx_grp, msg_ft],
    )
