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


def _cod_municipio_de_choice(municipio_choice: str) -> str | None:
    m = re.search(r"\(INE (\d+)\)", municipio_choice or "")
    return m.group(1) if m else None


def _listar_hablantes(cod_municipio: str) -> list[dict]:
    """Hablantes ya registrados para un municipio (código INE), ordenados por código."""
    from data.db import get_session, Hablante, Municipio as MunicipioModel
    with get_session() as s:
        mun = s.query(MunicipioModel).filter_by(codigo_ine=cod_municipio).first()
        if not mun:
            return []
        habs = (
            s.query(Hablante)
            .filter_by(municipio_id=mun.id)
            .order_by(Hablante.codigo)
            .all()
        )
        return [{"codigo": h.codigo, "edad": h.edad, "genero": h.genero} for h in habs]


def _siguiente_codigo_hablante(existentes: list[str]) -> str:
    usados = {int(c) for c in existentes if c.isdigit()}
    i = 1
    while i in usados:
        i += 1
    return str(i).zfill(2)


def hablantes_choices_ui(municipio_choice: str):
    """Refresca el desplegable de hablantes y el código sugerido para uno nuevo."""
    cod_municipio = _cod_municipio_de_choice(municipio_choice)
    if not cod_municipio:
        return gr.update(choices=[], value=None), gr.update(value="01")
    habs = _listar_hablantes(cod_municipio)
    choices = [
        (f"{h['codigo']} ({h['genero'] or '?'}, {h['edad'] or '?'} años)", h["codigo"])
        for h in habs
    ]
    siguiente = _siguiente_codigo_hablante([h["codigo"] for h in habs])
    valor = choices[0][1] if choices else None
    return gr.update(choices=choices, value=valor), gr.update(value=siguiente)


def registrar_hablante_ui(municipio_choice: str, codigo: str, edad, genero: str):
    """Da de alta un hablante nuevo para el municipio seleccionado."""
    cod_municipio = _cod_municipio_de_choice(municipio_choice)
    if not cod_municipio:
        return gr.update(), gr.update(), "Selecciona primero un municipio."

    codigo = (codigo or "").strip()
    if not re.match(r"^\d{2}$", codigo):
        return gr.update(), gr.update(), "El código del hablante debe tener exactamente 2 dígitos (por ejemplo, 01)."

    # Validación de edad: si se indica, debe ser un valor plausible.
    edad_val = None
    if edad is not None and str(edad).strip() != "":
        try:
            edad_val = int(edad)
        except (TypeError, ValueError):
            return gr.update(), gr.update(), "La edad debe ser un número entero."
        if not (0 <= edad_val <= 120):
            return gr.update(), gr.update(), "La edad debe estar entre 0 y 120 años."

    from data.db import get_session, Hablante, Municipio as MunicipioModel
    from sqlalchemy.exc import IntegrityError
    try:
        with get_session() as s:
            mun = s.query(MunicipioModel).filter_by(codigo_ine=cod_municipio).first()
            if not mun:
                return gr.update(), gr.update(), "No se ha encontrado el municipio en la base de datos."
            existe = s.query(Hablante).filter_by(municipio_id=mun.id, codigo=codigo).first()
            if existe:
                return gr.update(), gr.update(), f"Ya existe un hablante con el código {codigo} en este municipio."
            s.add(Hablante(
                municipio_id=mun.id,
                codigo=codigo,
                edad=edad_val,
                genero=genero or None,
            ))
        msg = f"Hablante {codigo} registrado correctamente."
    except IntegrityError:
        return gr.update(), gr.update(), f"Ya existe un hablante con el código {codigo} en este municipio."
    except Exception as e:
        return gr.update(), gr.update(), f"No se ha podido registrar el hablante: {e}"

    hab_upd, siguiente_upd = hablantes_choices_ui(municipio_choice)
    hab_upd["value"] = codigo
    return hab_upd, siguiente_upd, msg


def municipios_provincia_ui(provincia_choice: str, municipios_nga: dict):
    """Rellena el desplegable de municipio con todos los de la provincia elegida.

    El propio gr.Dropdown ya permite escribir para filtrar entre las opciones
    cargadas, así que no hace falta un cuadro de búsqueda ni texto previo.
    """
    if not provincia_choice:
        return gr.update(choices=[], value=None)
    cod_prov = provincia_choice.split(" - ")[0].strip()
    try:
        todos = get_municipios()
        resultados = sorted(
            (m for m in todos if m["codigo_ine"].startswith(cod_prov)),
            key=lambda m: m["nombre"],
        )
        opciones = [f"{m['nombre']} (INE {m['codigo_ine']})" for m in resultados]
    except Exception:
        from core.nga import buscar_municipio_nga
        resultados_nga = sorted(
            buscar_municipio_nga(municipios_nga, "", cod_prov),
            key=lambda x: x[1],
        )
        opciones = [f"{nom} (INE {cod})" for cod, nom, _ in resultados_nga]
    return gr.update(choices=opciones, value=None)


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
    if not hablante_id or not re.match(r"^\d{2}$", str(hablante_id).strip()):
        yield L("Error: selecciona un hablante (o registra uno nuevo)."); return

    cod_prov      = provincia_choice.split(" - ")[0].strip()
    provincia     = PROVINCIAS_DISPLAY[cod_prov]
    m             = re.search(r"\(INE (\d+)\)", municipio_choice)
    if not m:
        yield L("Error: municipio no valido."); return
    cod_municipio = m.group(1)
    municipio     = municipio_choice.split(" (INE")[0].strip()
    hablante_id   = str(hablante_id).strip()

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
    audio_grabado = gr.State(None)

    # El componente de grabación se monta bajo demanda (gr.render) en vez de
    # crearse oculto (gr.Group visible=False): en Gradio, un Audio con
    # sources=["microphone"] que nace oculto no inicializa bien el JS del
    # micrófono y se queda pillado en el estado de grabación (parar/reanudar,
    # sin llegar nunca a guardar el fichero) aunque luego se haga visible.
    @gr.render(inputs=modo_entrada_radio)
    def _render_grabar_panel(modo):
        if modo != "Grabar con micrófono":
            return
        audio_comp = gr.Audio(
            sources=["microphone"], type="filepath",
            label="Graba el audio directamente desde el micrófono",
        )
        gr.Markdown(
            "_Al pulsar **Parar** el audio ya queda guardado, no hace falta "
            "ningún botón adicional. La duración puede aparecer como 0:00 "
            "por un detalle visual de Gradio; no afecta al audio grabado. "
            "Usa la ❌ para descartarlo y grabar de nuevo._"
        )
        audio_comp.change(lambda x: x, inputs=audio_comp, outputs=audio_grabado)

    def cambiar_modo(modo):
        return (
            gr.update(visible=modo == "Escribir ruta de carpeta"),
            gr.update(visible=modo == "Arrastrar archivos"),
        )

    modo_entrada_radio.change(
        cambiar_modo, inputs=modo_entrada_radio,
        outputs=[carpeta_panel, drag_panel],
    )

    gr.Markdown("### Localizacion y hablante")
    with gr.Row():
        prov_dd = gr.Dropdown(choices=provincias_choices, label="Provincia")
        mun_dd  = gr.Dropdown(
            choices=[], label="Municipio",
            info="Escribe para filtrar entre los municipios de la provincia elegida",
        )

    prov_dd.change(
        lambda p: municipios_provincia_ui(p, municipios_nga),
        inputs=prov_dd, outputs=mun_dd,
    )

    with gr.Row():
        hab_dd = gr.Dropdown(choices=[], label="Hablante", info="Hablantes ya registrados en este municipio")

    with gr.Accordion("Registrar nuevo hablante", open=False):
        with gr.Row():
            nuevo_cod_in    = gr.Textbox(label="Código (2 dígitos)", value="01", scale=1)
            nuevo_edad_in   = gr.Number(label="Edad", precision=0, minimum=0, maximum=120, scale=1)
            nuevo_genero_dd = gr.Dropdown(choices=["M", "F", "X"], label="Género", scale=1)
            btn_nuevo_hab   = gr.Button("Registrar hablante", scale=1)
        msg_nuevo_hab = gr.Markdown()

    mun_dd.change(hablantes_choices_ui, inputs=mun_dd, outputs=[hab_dd, nuevo_cod_in])

    btn_nuevo_hab.click(
        registrar_hablante_ui,
        inputs=[mun_dd, nuevo_cod_in, nuevo_edad_in, nuevo_genero_dd],
        outputs=[hab_dd, nuevo_cod_in, msg_nuevo_hab],
    )

    gr.Markdown("### Parametros de procesado")
    with gr.Row():
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
        inputs=[carpeta_in, archivos_drag, audio_grabado, modo_entrada_radio, prov_dd, mun_dd, hab_dd, wmodel_dd, modo_proc_dd, prompt_chk],
        outputs=log_proc,
    )