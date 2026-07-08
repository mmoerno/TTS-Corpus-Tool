"""
ui/api_client.py
Cliente HTTP para la API REST. Centraliza token y llamadas.
"""
import os
import requests

API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8000")
_token: str | None = None

def set_token(token: str):
    global _token
    _token = token


def get_token() -> str | None:
    return _token


def _headers() -> dict:
    if _token:
        return {"Authorization": f"Bearer {_token}"}
    return {}


def login(uvus: str, password: str) -> str:
    """Devuelve el JWT o lanza excepción."""
    urls = [f"{API_BASE}/auth/login"]
    if "localhost" in API_BASE:
        urls.append(API_BASE.replace("localhost", "127.0.0.1") + "/auth/login")
    last_exception = None

    for url in urls:
        try:
            print(f"[api_client] login: url={url}, uvus={uvus}")
            r = requests.post(
                url,
                data={"username": uvus, "password": password},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10,
            )
            print(f"[api_client] response status={r.status_code}")
            print(f"[api_client] response body={r.text}")
            r.raise_for_status()
            token = r.json()["access_token"]
            set_token(token)
            return token
        except requests.exceptions.HTTPError as exc:
            last_exception = exc
            if r.status_code == 404:
                print(f"[api_client] 404 en {url}, probando next fallback si existe")
                continue
            raise
        except requests.exceptions.RequestException as exc:
            last_exception = exc
            print(f"[api_client] login exception: {exc}")
            continue

    raise last_exception or RuntimeError("Error desconocido en login")


# ── Clips ──────────────────────────────────────────────────────────────────

def get_clips(activo=True, split=None, hablante_id=None) -> list[dict]:
    params = {"activo": activo}
    if split:
        params["split"] = split
    if hablante_id:
        params["hablante_id"] = hablante_id
    r = requests.get(f"{API_BASE}/clips", params=params, headers=_headers(), timeout=15)
    r.raise_for_status()
    return r.json()


def registrar_clip(nombre_archivo: str, transcripcion: str, hablante_id: int,
                   duracion_s: float = None, split: str = None) -> dict:
    payload = {
        "nombre_archivo": nombre_archivo,
        "transcripcion": transcripcion,
        "hablante_id": hablante_id,
        "duracion_s": duracion_s,
        "split": split,
    }
    r = requests.post(f"{API_BASE}/clips", json=payload, headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def actualizar_transcripcion(clip_id: int, transcripcion: str) -> dict:
    r = requests.put(
        f"{API_BASE}/clips/{clip_id}",
        json={"transcripcion": transcripcion},
        headers=_headers(),
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def borrar_clip_api(clip_id: int):
    r = requests.delete(f"{API_BASE}/clips/{clip_id}", headers=_headers(), timeout=10)
    r.raise_for_status()


# ── Transcripciones ────────────────────────────────────────────────────────

def get_pendientes(hablante_id=None, limit=200) -> list[dict]:
    params = {"limit": limit}
    if hablante_id:
        params["hablante_id"] = hablante_id
    r = requests.get(f"{API_BASE}/transcripciones/pendientes",
                     params=params, headers=_headers(), timeout=15)
    r.raise_for_status()
    return r.json()


def get_historial(clip_id: int) -> list[dict]:
    r = requests.get(f"{API_BASE}/transcripciones/historial/{clip_id}",
                     headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


# ── Splits ─────────────────────────────────────────────────────────────────

def generar_splits(train_ratio: float = 0.85) -> dict:
    r = requests.post(f"{API_BASE}/splits/generar",
                      params={"train_ratio": train_ratio},
                      headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def export_split(formato: str, split: str = "train") -> bytes:
    r = requests.get(f"{API_BASE}/splits/export/{formato}",
                     params={"split": split},
                     headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.content


# ── Usuarios ───────────────────────────────────────────────────────────────

def get_me() -> dict:
    r = requests.get(f"{API_BASE}/auth/me", headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def listar_usuarios() -> list[dict]:
    r = requests.get(f"{API_BASE}/auth/usuarios", headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def crear_usuario_api(uvus: str, nombre: str, rol: str, password: str) -> dict:
    payload = {"uvus": uvus, "nombre": nombre, "rol": rol, "password": password}
    r = requests.post(f"{API_BASE}/auth/usuarios", json=payload, headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def desactivar_usuario_api(uvus: str) -> dict:
    r = requests.patch(f"{API_BASE}/auth/usuarios/{uvus}/desactivar", headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def cambiar_password_propia(password_actual: str, password_nuevo: str) -> None:
    payload = {"password_actual": password_actual, "password_nuevo": password_nuevo}
    r = requests.post(f"{API_BASE}/auth/cambiar-password", json=payload, headers=_headers(), timeout=10)
    r.raise_for_status()


# ── Hablantes / Municipios ─────────────────────────────────────────────────

def get_municipios(provincia_id=None) -> list[dict]:
    params = {}
    if provincia_id:
        params["provincia_id"] = provincia_id
    r = requests.get(f"{API_BASE}/municipios", params=params,
                     headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()