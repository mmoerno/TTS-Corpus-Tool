import csv
import re
from pathlib import Path

import gradio as gr
from config import (
    OUTPUT_ROOT, WHISPER_CACHE, WHISPER_MODELOS, WHISPER_DEFAULT,
    PROVINCIAS_DISPLAY, HEADER_LOCAL, LANG, AV_EXTS, GLOBAL_CSV, HEADER_GLOBAL,
)
from procesar_audios_andalucia import (
    segment_by_silence, convert_direct, get_duration_s,
    build_whisper_prompt, guardar_toponimos_txt,
    load_existing, migrate_csv,
)
from data.csv_store import split_train_eval, append_to_global
from ui.api_client import get_municipios, registrar_clip


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _siguiente_yy(wav_dir: Path, cod_municipio: str, hablante_id: str) -> int:
    """Calcula el siguiente índice YY mirando los WAVs existentes del hablante."""
    patron = f"{cod_municipio}_{hablante_id}_"
    indices = []
    for f in wav_dir.glob(f"{patron}*.wav"):
        partes = f.stem.split("_")
        if len(partes) >= 3:
            try:
                indices.append(int(partes[2]))
            except ValueError:
                pass
    return max(indices) + 1 if indices else 1


def _finalizar_splits(local_csv, mun_dir, municipio, provincia, log):
    from data.csv_store import rotate_backup, split_train_eval, append_to_global, HEADER_GLOBAL
    from procesar_audios_andalucia import GLOBAL_CSV
    train_p = mun_dir / "metadata_train.csv"
    eval_p  = mun_dir / "metadata_eval.csv"
    rotate_backup(train_p)
    rotate_backup(eval_p)
    n_tr, n_ev = split_train_eval(local_csv, train_p, eval_p, HEADER_LOCAL)
    log.append(f"Split local -> Train: {n_tr} | Eval: {n_ev}")
    GLOBAL_CSV.parent.mkdir(parents=True, exist_ok=True)
    append_to_global(GLOBAL_CSV, local_csv, municipio, provincia)
    split_train_eval(
        GLOBAL_CSV,
        GLOBAL_CSV.parent / "metadata_global_train.csv",
        GLOBAL_CSV.parent / "metadata_global_eval.csv",
        HEADER_GLOBAL,
    )
    log.append(f"CSV global actualizado: {GLOBAL_CSV}")


def buscar_municipios_ui(provincia_choice: str, texto: str, municipios_nga: dict):
    if not provincia_choice or not texto.strip():
        return gr.update(choices=[], value=None)
    cod_prov = provincia_choice.split(" - ")[0].strip()
    try:
        todos = get_municipios()
        resultados = [
            m for m in todos
            if texto.strip().lower() in m["nombre"].lower()
            and m["codigo_ine"].startswith(cod_prov)
        ]
        opciones = [f"{m['nombre']} (INE {m['codigo_ine']})" for m in resultados[:20]]
    except Exception:
        from core.nga import buscar_municipio_nga
        resultados_nga = buscar_municipio_nga(municipios_nga, texto.strip(), cod_prov)
        opciones = [f"{nom} (INE {cod})" for cod, nom, _ in resultados_nga]
    return gr.update(choices=opciones, value=opciones[0] if opciones else None)


def _registrar_en_bd(nombre_archivo: str, transcripcion: str,
                     hablante_id_bd: int, duracion_s: float):
    """Registra el clip en la BD via API. Fallo silencioso."""
    try:
        registrar_clip(
            nombre_archivo=nombre_archivo,
            transcripcion=transcripcion,
            hablante_id=hablante_id_bd,
            duracion_s=duracion_s,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Procesado principal
# ---------------------------------------------------------------------------

def procesar_audios(
    carpeta_texto, archivos_drag, audio_grabado, modo_entrada,
    provincia_choice, municipio_choice,
    hablante_id, whisper_model, modo_proc,
    nga_toponimos: dict,
    usar_prompt_toponimos: bool = True,
    progress=gr.Progress(track_tqdm=True),
):
    log = []

    def L(msg):
        log.append(msg)
        return "\n".join(log)

    if modo_entrada == "Arrastrar archivos":
        if not archivos_drag:
            yield L("Error: no se han cargado archivos.")
            return
        files = [Path(f.name) for f in archivos_drag]
    elif modo_entrada == "Grabar con micrófono":
        if not audio_grabado:
            yield L("Error: no se ha grabado ningún audio.")
            return
        files = [Path(audio_grabado)]
    else:
        if not carpeta_texto or not Path(carpeta_texto).exists():
            yield L("Error: la carpeta de entrada no existe.")
            return
        files = sorted(f for f in Path(carpeta_texto).iterdir() if f.suffix.lower() in AV_EXTS)

    if not files:
        yield L("Error: no se encontraron archivos de audio/video."); return
    if not provincia_choice:
        yield L("Error: selecciona una provincia."); return
    if not municipio_choice:
        yield L("Error: selecciona un municipio."); return
    if not re.match(r"^\d{2}$", hablante_id.strip()):
        yield L("Error: el ID de hablante debe tener exactamente 2 digitos (ej: 01)."); return

    cod_prov      = provincia_choice.split(" - ")[0].strip()
    provincia     = PROVINCIAS_DISPLAY[cod_prov]
    m             = re.search(r"\(INE (\d+)\)", municipio_choice)
    if not m:
        yield L("Error: municipio no valido."); return
    cod_municipio = m.group(1)
    municipio     = municipio_choice.split(" (INE")[0].strip()
    hablante_id   = hablante_id.strip()

    yield L(f"{len(files)} archivo(s) encontrado(s)")
    yield L(f"Municipio: {municipio} ({cod_municipio}) | Hablante: {hablante_id}")

    mun_dir   = OUTPUT_ROOT / provincia / f"{municipio}_{cod_municipio}"
    wav_dir   = mun_dir / "wavs"
    wav_dir.mkdir(parents=True, exist_ok=True)
    local_csv = mun_dir / "metadata.csv"

    # Topónimos NGA para prompt Whisper
    tops_mun = nga_toponimos.get(cod_municipio, [])
    if tops_mun:
        txt_out = mun_dir / f"toponimos_{cod_municipio}.txt"
        guardar_toponimos_txt(tops_mun, txt_out, cod_municipio)
        yield L(f"{len(tops_mun)} toponimos NGA cargados para {municipio}")
    else:
        yield L(f"Aviso: sin toponimos NGA para municipio {cod_municipio}")

    whisper_prompt = build_whisper_prompt(tops_mun) if usar_prompt_toponimos else ""
    if tops_mun:
        yield L(f"Prompt NGA: {'activado' if usar_prompt_toponimos else 'desactivado'}")

    # Buscar hablante_id en BD para registrar clips (fallo silencioso si no existe)
    hablante_id_bd = None
    try:
        from data.db import get_session, Hablante, Municipio as MunicipioModel
        with get_session() as s:
            mun_bd = s.query(MunicipioModel).filter_by(codigo_ine=cod_municipio).first()
            if mun_bd:
                hab = s.query(Hablante).filter_by(
                    municipio_id=mun_bd.id, codigo=hablante_id
                ).first()
                if hab:
                    hablante_id_bd = hab.id
    except Exception:
        pass

    # ── Modo sin transcripción ──────────────────────────────────────────────
    if modo_proc == "Solo crear carpetas y WAVs (sin transcribir)":
        migrate_csv(local_csv, HEADER_LOCAL)
        already  = load_existing(local_csv)
        csv_new  = not local_csv.exists()
        siguiente_yy = _siguiente_yy(wav_dir, cod_municipio, hablante_id)

        with open(local_csv, "a", newline="", encoding="utf-8") as cf:
            w = csv.writer(cf, delimiter="|")
            if csv_new:
                w.writerow(HEADER_LOCAL)
            for i, src in enumerate(progress.tqdm(files, desc="Convirtiendo"), siguiente_yy):
                base = f"{cod_municipio}_{hablante_id}_{str(i).zfill(2)}"
                dur  = get_duration_s(src)
                if dur > 15 or dur == 0.0:
                    try:
                        wavs = segment_by_silence(src, wav_dir, base)
                        yield L(f"  {src.name} -> {len(wavs)} segmentos")
                        for wav in wavs:
                            yield L(f"    + {wav.name}")
                    except Exception as e:
                        yield L(f"  Error VAD {src.name}: {e}"); continue
                else:
                    dst = wav_dir / f"{base}.wav"
                    if not dst.exists():
                        convert_direct(src, dst)
                    wavs = [dst]
                    yield L(f"  + {dst.name}")
                for wav in wavs:
                    key = f"wavs/{wav.name}"
                    if key not in already:
                        w.writerow([key, "", LANG])
                        already.add(key)
                        if hablante_id_bd:
                            _registrar_en_bd(key, "", hablante_id_bd, get_duration_s(wav))

        yield L(f"WAVs generados en {wav_dir}")
        yield L("Transcripciones vacias. Rellena en la pestana Revision.")
        _finalizar_splits(local_csv, mun_dir, municipio, provincia, log)
        yield "\n".join(log)
        return

    # ── Modo con Whisper ────────────────────────────────────────────────────
    yield L(f"Cargando modelo Whisper '{whisper_model}'...")
    try:
        import whisper as wmod
        model = wmod.load_model(whisper_model, download_root=str(WHISPER_CACHE))
        yield L(f"Modelo {whisper_model} listo.")
    except Exception as e:
        yield L(f"Error cargando Whisper: {e}"); return

    migrate_csv(local_csv, HEADER_LOCAL)
    already  = load_existing(local_csv)
    csv_new  = not local_csv.exists()
    nuevos   = omitidos = 0
    siguiente_yy = _siguiente_yy(wav_dir, cod_municipio, hablante_id)

    with open(local_csv, "a", newline="", encoding="utf-8") as cf:
        w = csv.writer(cf, delimiter="|")
        if csv_new:
            w.writerow(HEADER_LOCAL)
        for i, src in enumerate(progress.tqdm(files, desc="Procesando"), siguiente_yy):
            base = f"{cod_municipio}_{hablante_id}_{str(i).zfill(2)}"
            dur  = get_duration_s(src)
            if dur > 15 or dur == 0.0:
                try:
                    wavs = segment_by_silence(src, wav_dir, base)
                    yield L(f"  {src.name} -> {len(wavs)} segmentos")
                except Exception as e:
                    yield L(f"  Error VAD: {e}"); continue
            else:
                dst = wav_dir / f"{base}.wav"
                if not dst.exists():
                    convert_direct(src, dst)
                wavs = [dst]
            for wav in wavs:
                key = f"wavs/{wav.name}"
                if key in already:
                    omitidos += 1; continue
                yield L(f"  Transcribiendo {wav.name}...")
                try:
                    kwargs = dict(language="es", task="transcribe", fp16=False)
                    if whisper_prompt:
                        kwargs["initial_prompt"] = whisper_prompt
                    res        = model.transcribe(str(wav), **kwargs)
                    texto      = res["text"].strip()
                    duracion_s = get_duration_s(wav)
                    w.writerow([key, texto, LANG])
                    cf.flush()
                    already.add(key)
                    nuevos += 1
                    yield L(f'  ✓ {wav.name}: "{texto}"')
                    if hablante_id_bd:
                        _registrar_en_bd(key, texto, hablante_id_bd, duracion_s)
                except Exception as e:
                    yield L(f"  Error transcripcion {wav.name}: {e}")

    yield L(f"\nNuevos: {nuevos} | Omitidos: {omitidos}")
    _finalizar_splits(local_csv, mun_dir, municipio, provincia, log)
    yield "\n".join(log)


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def build_tab(provincias_choices: list, municipios_nga: dict, nga_toponimos: dict):
    gr.Markdown("### Fuente de audios")
    modo_entrada_radio = gr.Radio(
        choices=["Escribir ruta de carpeta", "Arrastrar archivos", "Grabar con micrófono"],
        value="Escribir ruta de carpeta", label="Modo de entrada",
    )
    carpeta_panel = gr.Group(visible=True)
    with carpeta_panel:
        carpeta_in = gr.Textbox(label="Ruta de la carpeta de entrada", placeholder="/ruta/a/mis/audios")
    drag_panel = gr.Group(visible=False)
    with drag_panel:
        archivos_drag = gr.File(
            label="Arrastra los archivos de audio aqui", file_count="multiple",
            file_types=[".wav",".mp3",".ogg",".opus",".m4a",".flac",".mp4",".mkv",".mov",".webm",".aac",".wma"],
        )
    grabar_panel = gr.Group(visible=False)
    with grabar_panel:
        audio_grabado = gr.Audio(
            sources=["microphone"], type="filepath",
            label="Graba el audio directamente desde el micrófono",
        )

    def cambiar_modo(modo):
        return (
            gr.update(visible=modo == "Escribir ruta de carpeta"),
            gr.update(visible=modo == "Arrastrar archivos"),
            gr.update(visible=modo == "Grabar con micrófono"),
        )

    modo_entrada_radio.change(
        cambiar_modo, inputs=modo_entrada_radio,
        outputs=[carpeta_panel, drag_panel, grabar_panel],
    )

    gr.Markdown("### Localizacion y hablante")
    with gr.Row():
        prov_dd    = gr.Dropdown(choices=provincias_choices, label="Provincia")
        mun_search = gr.Textbox(label="Buscar municipio", placeholder="Ej: utr")
        mun_dd     = gr.Dropdown(choices=[], label="Municipio")

    mun_search.change(
        lambda p, t: buscar_municipios_ui(p, t, municipios_nga),
        inputs=[prov_dd, mun_search], outputs=mun_dd,
    )

    gr.Markdown("### Parametros de procesado")
    with gr.Row():
        hab_in       = gr.Textbox(label="ID Hablante (2 digitos)", value="01", scale=1)
        wmodel_dd    = gr.Dropdown(choices=WHISPER_MODELOS, value=WHISPER_DEFAULT, label="Modelo Whisper", scale=1)
        modo_proc_dd = gr.Dropdown(
            choices=["Transcribir con Whisper (automatico)", "Solo crear carpetas y WAVs (sin transcribir)"],
            value="Transcribir con Whisper (automatico)", label="Modo de procesado", scale=2,
        )
        prompt_chk = gr.Checkbox(value=True, label="Usar prompt de toponimos NGA", scale=1)

    btn_proc = gr.Button("Iniciar procesado", variant="primary")
    log_proc = gr.Textbox(label="Log", lines=20, interactive=False, autoscroll=True)

    def _procesar(c, a, g, m, p, mu, h, wm, mp, pc):
        yield from procesar_audios(c, a, g, m, p, mu, h, wm, mp, nga_toponimos, pc)

    btn_proc.click(
        _procesar,
        inputs=[carpeta_in, archivos_drag, audio_grabado, modo_entrada_radio, prov_dd, mun_dd, hab_in, wmodel_dd, modo_proc_dd, prompt_chk],
        outputs=log_proc,
    )