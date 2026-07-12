import requests
import gradio as gr

from ui.api_client import cambiar_password_propia


def _error_detail(exc: Exception) -> str:
    if isinstance(exc, requests.exceptions.HTTPError):
        try:
            return exc.response.json().get("detail", str(exc))
        except Exception:
            return str(exc)
    return str(exc)


def cambiar_password(password_actual, password_nuevo, password_nuevo2):
    if not password_actual or not password_nuevo:
        return "Rellena la contraseña actual y la nueva."
    if password_nuevo != password_nuevo2:
        return "Las contraseñas nuevas no coinciden."
    try:
        cambiar_password_propia(password_actual, password_nuevo)
        return "Contraseña actualizada correctamente."
    except Exception as e:
        return f"Error: {_error_detail(e)}"


def build_tab():
    gr.Markdown("""
### Bienvenido a Andalucía TTS

Esta aplicación sirve para grabar audios con distintos acentos de Andalucía,
revisar lo que se ha entendido de cada audio y, cuando haya suficientes,
usarlos para entrenar una voz.

#### ¿Por dónde empiezo?

1. **Procesar audios** — sube o graba los audios y di de qué pueblo y provincia es quien habla.
2. **Revisar transcripciones** — el programa transcribe lo que se ha dicho; aquí corriges lo que no haya entendido bien.
3. **Estadísticas** — para ver de un vistazo cuántos audios hay y de dónde.
4. **Entrenar XTTS** — cuando haya suficientes audios revisados, se lanza el entrenamiento de la voz.
5. **Exportar datasets** — al terminar, se preparan los datos para usarlos fuera de la aplicación.

Si es tu primera vez, empieza por **Procesar audios** y luego pasa a
**Revisar transcripciones** antes de exportar nada.
""")

    gr.Markdown("### Cambiar mi contraseña")
    with gr.Row():
        pw_actual = gr.Textbox(label="Contraseña actual", type="password")
        pw_nueva  = gr.Textbox(label="Contraseña nueva", type="password")
        pw_nueva2 = gr.Textbox(label="Repetir contraseña nueva", type="password")
    btn_pw = gr.Button("Cambiar contraseña")
    msg_pw = gr.Markdown("")
    btn_pw.click(
        cambiar_password,
        inputs=[pw_actual, pw_nueva, pw_nueva2],
        outputs=msg_pw,
    )

    with gr.Accordion("Detalles técnicos (formatos de exportación, modos copy/symlink/reference, estructura de ficheros)", open=False):
        gr.Markdown("""
### Modelos y formatos disponibles

- `ljspeech`
  - Formato clásico: `audio|transcripcion`.
  - Ideal para proyectos legacy y para Coqui en modo simple.

- `commonvoice`
  - Formato TSV tipo Common Voice: `client_id|path|sentence`.
  - Útil para usar datos en pipelines que esperan estilo Common Voice.

- `csv`
  - Genera un CSV completo con las columnas esenciales.
  - Apto para procesos personalizados o modelos que acepten metadatos genéricos.

- `xtts`
  - Formato para Coqui XTTS v2: `audio_file|text|text_norm`.
  - Permite fine-tuning con splits de entrenamiento y evaluación.

- `piper`
  - Formato simple de `metadata.csv` para Piper/Rhasspy.
  - Puede incluir `id|text` o `id|speaker|text` según necesidad.

- `f5`
  - Formato de F5-TTS: rutas absolutas a WAV + texto.
  - Compatible con pipelines que leen CSVs de entrenamiento con rutas completas.

### Qué es `symlink` y `reference`

- `copy`
  - Copia los archivos WAV al directorio de exportación.
  - Es un export completamente independiente, pero usa más espacio.

- `symlink`
  - Crea enlaces simbólicos a los WAV originales.
  - Ahorra espacio al no duplicar audio.
  - En Windows puede requerir permisos de administrador o políticas especiales.

- `reference`
  - No copia ni enlaza archivos.
  - Los metadatos referencian directamente las rutas originales.
  - Es el modo más ligero, ideal si el dataset original ya está disponible para el entrenamiento.

### Estructura de ficheros esperada

- `dataset/` — datos procesados por provincia y municipio.
- `dataset/<provincia>/<municipio>_<INE>/wavs/` — archivos WAV procesados.
- `dataset/<provincia>/<municipio>_<INE>/metadata.csv` — metadatos básicos.
- `exports/<nombre_modelo>/` — salida de exportaciones por modelo.

### Recomendaciones rápidas

- Si quieres un paquete independiente, usa `copy`.
- Si buscas ahorrar espacio, usa `symlink` o `reference`.
- Para fine-tuning, genera siempre los splits `train` y `eval`.
""")
