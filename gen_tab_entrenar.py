#!/usr/bin/env python3
"""Generate the clean training tab file"""

content = '''import re
import csv
from pathlib import Path
import tempfile

import gradio as gr

from config import OUTPUT_ROOT, GLOBAL_CSV, HEADER_LOCAL, HEADER_GLOBAL, EXPORT_ROOT
from data.csv_store import split_train_eval
from data.export import export_dataset
from core.train import get_available_models, get_trainer_for_model


def get_provincias_procesadas():
    provs = set()
    for meta in OUTPUT_ROOT.rglob("metadata.csv"):
        prov = meta.parent.parent.name
        if prov and prov != str(OUTPUT_ROOT.name):
            provs.add(prov)
    return sorted(provs)


def get_hablantes_de_municipio(cod_ine: str) -> list:
    posibles = list(OUTPUT_ROOT.rglob(f"*_{cod_ine}/metadata.csv"))
    if not posibles:
        return []
    hablantes = set()
    with open(posibles[0], newline="", encoding="utf-8") as f:
        for row in csv.reader(f, delimiter="|"):
            if not row or row[0] == "audio":
                continue
            m = re.match(r"wavs/\\d+_(\\d{2})_", row[0])
            if m:
                hablantes.add(m.group(1))
    return sorted(hablantes)


def get_fuentes_entrenamiento():
    opciones = ["Dataset global (todos los municipios)"]
    for prov in get_provincias_procesadas():
        opciones.append(f"Provincia: {prov}")
    for meta in sorted(OUTPUT_ROOT.rglob("metadata.csv")):
        parts = meta.parent.name.rsplit("_", 1)
        if len(parts) != 2:
            continue
        nombre, cod = parts
        opciones.append(f"{nombre} (INE {cod})")
        for hab in get_hablantes_de_municipio(cod):
            opciones.append(f"Hablante {hab} — {nombre} (INE {cod})")
    return opciones


def get_modelos_disponibles():
    models = get_available_models()
    return {code: info["name"] for code, info in models.items()}


def get_opciones_entreno():
    return ["Local", "Servidor"]


def actualizar_nombre_modelo(fuente):
    if not fuente:
        return gr.update(value="modelo_andaluz_v1")
    if fuente.startswith("Dataset global"):
        return gr.update(value="modelo_andaluz_global_v1")
    if fuente.startswith("Provincia:"):
        prov = fuente.split("Provincia:")[1].strip().lower().replace(" ", "_")
        return gr.update(value=f"modelo_{prov}_v1")
    m_hab = re.match(r"Hablante (\\d{2}) — (.+) \\(INE (\\d+)\\)", fuente)
    if m_hab:
        return gr.update(value=f"modelo_{m_hab.group(2).lower().replace(' ','_')}_hab{m_hab.group(1)}_v1")
    m_ine = re.search(r"^(.+) \\(INE (\\d+)\\)$", fuente)
    if m_ine:
        return gr.update(value=f"modelo_{m_ine.group(1).lower().replace(' ','_')}_v1")
    return gr.update(value="modelo_andaluz_v1")


def actualizar_opciones_entreno(modelo_seleccionado):
    if not modelo_seleccionado:
        return gr.update(choices=["Local"], value="Local", interactive=False)
    models = get_available_models()
    for code, info in models.items():
        if info["name"] == modelo_seleccionado:
            opciones = []
            if info["supports_local"]:
                opciones.append("Local")
            if info["supports_remote"]:
                opciones.append("Servidor")
            is_interactive = len(opciones) > 1
            default_value = opciones[0] if opciones else "Local"
            return gr.update(choices=opciones, value=default_value, interactive=is_interactive)
    return gr.update(choices=["Local"], value="Local")


def resolver_fuente_csv(fuente: str):
    try:
        if fuente.startswith("Dataset global"):
            return OUTPUT_ROOT / "metadata_global.csv"
        elif fuente.startswith("Provincia:"):
            prov = fuente.split("Provincia:")[-1].strip()
            path = OUTPUT_ROOT / prov
            if (path / "metadata.csv").exists():
                return path / "metadata.csv"
        else:
            m = re.search(r"\\(INE (\\d+)\\)", fuente)
            if m:
                code = m.group(1)
                for meta in OUTPUT_ROOT.rglob(f"*_{code}/metadata.csv"):
                    return meta
    except Exception:
        pass
    return None


def generar_export_entrenamiento(fuente: str, modelo: str):
    csv_path = resolver_fuente_csv(fuente)
    if not csv_path or not csv_path.exists():
        return None, f"[ERROR] CSV no encontrado: {fuente}"
    models = get_available_models()
    model_code = None
    for code, info in models.items():
        if info["name"] == modelo:
            model_code = code
            break
    if not model_code:
        return None, f"[ERROR] Modelo no reconocido: {modelo}"
    export_dir = Path(tempfile.mkdtemp(prefix=f"train_{model_code}_"))
    try:
        export_dataset(csv_path, export_dir, [model_code], copy_mode="symlink", split="all")
        model_dir = export_dir / model_code
        if model_code == "xtts":
            train_csv = model_dir / "metadata_train.csv"
            eval_csv = model_dir / "metadata_eval.csv"
        else:
            train_csv = model_dir / "metadata.csv"
            eval_csv = None
        if not train_csv.exists():
            return None, "[ERROR] CSV no generado"
        return {
            "model": model_code,
            "export_dir": export_dir,
            "train_csv": train_csv,
            "eval_csv": eval_csv,
            "wavs_dir": model_dir / "wavs",
        }, None
    except Exception as e:
        return None, f"[ERROR] {e}"


def verificar_dataset_modelo(fuente, modelo):
    export_config, error = generar_export_entrenamiento(fuente, modelo)
    if error:
        return error, gr.update(interactive=False)
    try:
        with open(export_config["train_csv"], "r", encoding="utf-8") as f:
            n_clips = len(f.readlines()) - 1
        models = get_available_models()
        model_code = export_config["model"]
        min_duration = models[model_code]["recommended_min_duration_min"]
        estimated_minutes = (n_clips * 4.5) / 60
        status = "OK" if estimated_minutes >= min_duration else "Limitado"
        msg = f"**{modelo}**\\nClips: {n_clips}\\nDuración: ~{estimated_minutes:.1f} min\\nMínimo: {min_duration} min\\nEstado: {status}"
        return msg, gr.update(interactive=True)
    except Exception as e:
        return f"[ERROR] {e}", gr.update(interactive=False)


def iniciar_entrenamiento_modelo(fuente, modelo, modo_entreno, epochs, batch_size, lr, model_name, progress=gr.Progress()):
    log = []
    def L(msg):
        log.append(msg)
        return "\\n".join(log)
    if not fuente or not modelo or not modo_entreno:
        yield L("[ERROR] Faltan parámetros")
        return
    yield L(f"Modelo: {modelo}")
    yield L(f"Fuente: {fuente}")
    yield L(f"Modo: {modo_entreno}")
    export_config, error = generar_export_entrenamiento(fuente, modelo)
    if error:
        yield L(error)
        return
    model_code = export_config["model"]
    try:
        trainer_class = get_trainer_for_model(model_code)
    except ValueError as e:
        yield L(f"[ERROR] {e}")
        return
    output_dir = Path(EXPORT_ROOT) / "modelos" / model_name
    train_config = {
        "train_csv": str(export_config["train_csv"]),
        "eval_csv": str(export_config["eval_csv"]) if export_config["eval_csv"] else None,
        "output_dir": str(output_dir),
        "epochs": int(epochs),
        "batch_size": int(batch_size),
        "lr": str(lr),
    }
    if modo_entreno == "Local":
        yield L("Entrenando localmente...")
        success, logs = trainer_class.train_local(train_config)
        for msg in logs:
            yield L(msg)
    else:
        yield L("Conectando con servidor...")
        success, logs = trainer_class.train_remote(train_config, "http://127.0.0.1:8000")
        for msg in logs:
            yield L(msg)


def build_tab():
    gr.Markdown("### Entrenamiento de Modelos TTS")
    gr.Markdown("Entrena modelos de síntesis de voz con tus datos.\\n\\n**Soportados:**\\n- XTTS v2\\n- Piper\\n- F5-TTS")
    
    with gr.Row():
        fuente_dd = gr.Dropdown(
            choices=get_fuentes_entrenamiento(),
            value="Dataset global (todos los municipios)",
            label="Fuente de datos",
            scale=3
        )
        btn_refresh = gr.Button("Actualizar", scale=1)
    
    btn_refresh.click(
        lambda: gr.update(choices=get_fuentes_entrenamiento()),
        outputs=fuente_dd
    )
    
    with gr.Row():
        modelo_dd = gr.Dropdown(
            choices=list(get_modelos_disponibles().values()),
            value="XTTS v2",
            label="Modelo",
            scale=2
        )
        modo_dd = gr.Dropdown(
            choices=["Local", "Servidor"],
            value="Local",
            label="Modo entrenamiento",
            scale=1
        )
    
    btn_verify = gr.Button("Verificar dataset")
    msg_verify = gr.Markdown("")
    
    with gr.Row():
        epochs_sl = gr.Slider(1, 100, 10, label="Épocas")
        batch_sl = gr.Slider(1, 32, 4, label="Batch size")
        lr_tx = gr.Textbox("5e-6", label="Learning rate")
    
    model_name_tx = gr.Textbox("modelo_andaluz_v1", label="Nombre modelo")
    
    btn_train = gr.Button("Entrenar modelo", variant="primary", interactive=False)
    log_train = gr.Textbox(label="Log", lines=18, interactive=False, autoscroll=True)
    
    fuente_dd.change(actualizar_nombre_modelo, fuente_dd, model_name_tx)
    modelo_dd.change(actualizar_opciones_entreno, modelo_dd, modo_dd)
    btn_verify.click(verificar_dataset_modelo, [fuente_dd, modelo_dd], [msg_verify, btn_train])
    btn_train.click(iniciar_entrenamiento_modelo, [fuente_dd, modelo_dd, modo_dd, epochs_sl, batch_sl, lr_tx, model_name_tx], log_train)
'''

with open("ui/tab_entrenar.py", "w", encoding="utf-8") as f:
    f.write(content)

print("✅ File generated successfully")
