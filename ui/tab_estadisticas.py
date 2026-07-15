from collections import defaultdict
from pathlib import Path

import gradio as gr
from config import OUTPUT_ROOT
from ui.api_client import get_clips, generar_splits


def vaciar_eliminados():
    total = 0
    for d in OUTPUT_ROOT.rglob("_eliminados"):
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.is_file():
                try:
                    f.unlink(); total += 1
                except Exception:
                    pass
    return f"Se han eliminado {total} archivo(s) de las carpetas _eliminados/."


def calcular_estadisticas():
    try:
        clips = get_clips(activo=True)
    except Exception as e:
        return f"Error API: {e}", None
    if not clips:
        return "Sin datos todavia.", None

    stats = defaultdict(lambda: {"clips": 0, "duracion": 0.0})
    sin_duracion = 0

    for c in clips:
        partes = Path(c["nombre_archivo"]).stem.split("_")
        hab = partes[1] if len(partes) >= 2 else "??"
        stats[hab]["clips"] += 1
        if c.get("duracion_s"):
            stats[hab]["duracion"] += c["duracion_s"]
        else:
            sin_duracion += 1

    total_c = sum(s["clips"] for s in stats.values())
    total_s = sum(s["duracion"] for s in stats.values())
    total_m = round(total_s / 60, 1)

    tabla = []
    for hab in sorted(stats):
        s = stats[hab]
        mins = round(s["duracion"] / 60, 1)
        estado = "Insuficiente" if mins < 10 else ("Limitado" if mins < 30 else "OK")
        tabla.append([hab, s["clips"], f"{mins} min", estado])

    aviso = f" _(⚠ {sin_duracion} clips sin duración registrada)_" if sin_duracion else ""
    resumen = (
        f"**Total clips:** {total_c} | "
        f"**Duración real:** ~{total_m} min | "
        f"**Mínimo XTTS recomendado:** 30 min/hablante{aviso}"
    )
    return resumen, tabla

def reconstruir_splits():
    try:
        resultado = generar_splits()
        msg = f"Splits generados → Train: {resultado['train']} | Eval: {resultado['eval']} | Total: {resultado['asignados']}"
    except Exception as e:
        msg = f"Error API: {e}"
    resumen, tabla = calcular_estadisticas()
    return resumen + f"\n\n_{msg}_", tabla


def build_tab():
    gr.Markdown("### Estado del dataset por hablante")
    with gr.Row():
        btn_stats = gr.Button("Calcular estadisticas", variant="primary")
        btn_rebuild = gr.Button("Generar particiones (entrenamiento/validación)", variant="secondary")
        btn_vaciar = gr.Button("Vaciar la papelera de clips descartados", variant="stop")
    resumen_stats = gr.Markdown("")
    tabla_stats = gr.Dataframe(
        headers=["Hablante", "Clips", "Duracion real", "Estado"],
        interactive=False,
    )
    btn_stats.click(calcular_estadisticas, outputs=[resumen_stats, tabla_stats])
    btn_rebuild.click(reconstruir_splits, outputs=[resumen_stats, tabla_stats])
    btn_vaciar.click(vaciar_eliminados, outputs=resumen_stats)