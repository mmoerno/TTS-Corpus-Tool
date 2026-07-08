"""
ui/tab_usuarios.py
Pestaña de gestión de usuarios (solo administradores): listar, crear con rol
y desactivar cuentas. Requiere que quien la use tenga rol 'admin' en la API;
la pestaña se oculta a otros roles desde gui_tts_andaluz.py.
"""

import requests
import gradio as gr

from ui.api_client import (
    listar_usuarios,
    crear_usuario_api,
    desactivar_usuario_api,
)

ROLES = ["recolector", "revisor", "admin"]


def _error_detail(exc: Exception) -> str:
    if isinstance(exc, requests.exceptions.HTTPError):
        try:
            return exc.response.json().get("detail", str(exc))
        except Exception:
            return str(exc)
    return str(exc)


def refrescar_tabla():
    try:
        usuarios = listar_usuarios()
    except Exception as e:
        return f"Error: {_error_detail(e)}", None
    tabla = [
        [u["uvus"], u["nombre"], u["rol"], "Sí" if u["activo"] else "No"]
        for u in usuarios
    ]
    return f"{len(usuarios)} usuario(s) registrados.", tabla


def crear_usuario(uvus, nombre, rol, password, password2):
    if not uvus or not nombre or not password:
        resumen, tabla = refrescar_tabla()
        return "Rellena UVUS, nombre y contraseña.", resumen, tabla
    if password != password2:
        resumen, tabla = refrescar_tabla()
        return "Las contraseñas no coinciden.", resumen, tabla
    if rol not in ROLES:
        resumen, tabla = refrescar_tabla()
        return "Rol no válido.", resumen, tabla
    try:
        crear_usuario_api(uvus, nombre, rol, password)
        msg = f"Usuario '{uvus}' creado con rol '{rol}'."
    except Exception as e:
        msg = f"Error: {_error_detail(e)}"
    resumen, tabla = refrescar_tabla()
    return msg, resumen, tabla


def desactivar_usuario(uvus):
    if not uvus:
        resumen, tabla = refrescar_tabla()
        return "Introduce el UVUS del usuario a desactivar.", resumen, tabla
    try:
        desactivar_usuario_api(uvus)
        msg = f"Usuario '{uvus}' desactivado."
    except Exception as e:
        msg = f"Error: {_error_detail(e)}"
    resumen, tabla = refrescar_tabla()
    return msg, resumen, tabla


def build_tab():
    gr.Markdown(
        "### Usuarios registrados\n"
        "Pestaña visible solo para administradores. Los roles disponibles son "
        "`recolector` (sube y transcribe sus propios audios), `revisor` (además "
        "corrige transcripciones de cualquier clip) y `admin` (acceso completo)."
    )
    resumen_md = gr.Markdown("")
    tabla_usuarios = gr.Dataframe(
        headers=["UVUS", "Nombre", "Rol", "Activo"],
        interactive=False,
    )
    btn_refrescar = gr.Button("Refrescar lista")

    gr.Markdown("### Crear nuevo usuario")
    with gr.Row():
        uvus_in = gr.Textbox(label="UVUS (identificador único)")
        nombre_in = gr.Textbox(label="Nombre completo")
    with gr.Row():
        rol_dd = gr.Dropdown(choices=ROLES, value="recolector", label="Rol")
        password_in = gr.Textbox(label="Contraseña", type="password")
        password2_in = gr.Textbox(label="Repetir contraseña", type="password")
    btn_crear = gr.Button("Crear usuario", variant="primary")
    msg_crear = gr.Markdown("")

    gr.Markdown("### Desactivar usuario")
    gr.Markdown(
        "_No borra la cuenta ni su historial: solo le impide volver a iniciar "
        "sesión. Introduce el UVUS exacto tal como aparece en la tabla de arriba._"
    )
    with gr.Row():
        uvus_desactivar_in = gr.Textbox(label="UVUS a desactivar")
        btn_desactivar = gr.Button("Desactivar", variant="stop")

    btn_refrescar.click(refrescar_tabla, outputs=[resumen_md, tabla_usuarios])
    btn_crear.click(
        crear_usuario,
        inputs=[uvus_in, nombre_in, rol_dd, password_in, password2_in],
        outputs=[msg_crear, resumen_md, tabla_usuarios],
    )
    btn_desactivar.click(
        desactivar_usuario,
        inputs=[uvus_desactivar_in],
        outputs=[msg_crear, resumen_md, tabla_usuarios],
    )
