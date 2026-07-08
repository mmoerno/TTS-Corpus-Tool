import re
import gradio as gr
from pathlib import Path
from config import OUTPUT_ROOT, EXPORT_ROOT
from data.export import export_dataset
from ui.tab_entrenar import get_fuentes_entrenamiento


def generar_export(fuente: str, formatos: list, nombre_modelo: str, copy_mode: str, split: str):
    log = []

    def L(m):
        log.append(m)
        return "\n".join(log)

    if not fuente:
        return L("Error: selecciona una fuente de datos.")
    if not formatos:
        return L("Error: selecciona al menos un formato.")
    if not nombre_modelo:
        return L("Error: escribe un nombre de modelo/presets para la carpeta de salida.")

    L(f"Fuente: {fuente}")
    L(f"Formatos: {', '.join(formatos)}")
    out_base = EXPORT_ROOT / nombre_modelo
    out_base.mkdir(parents=True, exist_ok=True)

    hablante_prefix = None
    try:
        if fuente.startswith("Dataset global"):
            csv_path = OUTPUT_ROOT / "metadata_global.csv"
        elif fuente.startswith("Provincia:"):
            prov = fuente.split("Provincia:")[-1].strip()
            csv_path = OUTPUT_ROOT / prov / "metadata.csv"
        else:
            m2 = re.search(r"Hablante (\d{2}) — .+\(INE (\d+)\)", fuente)
            m1 = re.search(r"^(.+) \(INE (\d+)\)$", fuente)
            if m2:
                hab  = m2.group(1)
                code = m2.group(2)
                csv_path = next(OUTPUT_ROOT.rglob(f"*_{code}/metadata.csv"))
                hablante_prefix = f"wavs/{code}_{hab}_"
            elif m1:
                code = m1.group(2)
                csv_path = next(OUTPUT_ROOT.rglob(f"*_{code}/metadata.csv"))
            else:
                return L("Error: no se pudo resolver la ruta de origen.")
    except StopIteration:
        return L("Error: CSV fuente no encontrado.")

    L(f"CSV origen: {csv_path}")
    if hablante_prefix:
        L(f"Filtro hablante: {hablante_prefix}")

    try:
        export_dataset(
            csv_path, out_base, formatos,
            copy_mode=copy_mode,
            split=split,
            hablante_prefix=hablante_prefix,
        )
        L(f"Export completado en {out_base}")
    except Exception as e:
        return L(f"Error durante export: {e}")

    return L("Hecho.")


def build_tab(provincias_choices: list, municipios_nga: dict):
    gr.Markdown("### Exportar datasets para entrenamiento")
    with gr.Row():
        fuente_dd = gr.Dropdown(choices=get_fuentes_entrenamiento(), label="Fuente a exportar", scale=3)
        btn_refresh = gr.Button("Actualizar")
    btn_refresh.click(lambda: gr.update(choices=get_fuentes_entrenamiento()), outputs=fuente_dd)

    formatos = gr.CheckboxGroup(
        choices=["ljspeech", "commonvoice", "csv", "xtts", "piper", "f5"],
        label="Formatos a generar",
    )
    nombre_modelo = gr.Textbox(label="Nombre de carpeta/modelo (salida)", value="export_model")
    copy_mode = gr.Radio(choices=["copy", "symlink", "reference"], value="copy", label="Modo de manejo de WAVs")
    split = gr.Dropdown(choices=["all", "train", "eval"], value="all", label="Split a exportar")
    btn_gen = gr.Button("Generar export", variant="primary")
    log = gr.Textbox(label="Log", lines=12, interactive=False, autoscroll=True)

    btn_gen.click(generar_export, inputs=[fuente_dd, formatos, nombre_modelo, copy_mode, split], outputs=log)