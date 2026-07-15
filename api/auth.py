"""
api/auth.py
Gestión de JWT y dependencias de autenticación para FastAPI.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pathlib import Path

from data.db import get_session, Usuario

load_dotenv(Path(__file__).parent.parent / ".env")

_default_secret = "cambia_esto_en_produccion_ahora"
SECRET_KEY = os.getenv("JWT_SECRET", _default_secret)
if SECRET_KEY == _default_secret:
    raise RuntimeError(
        "JWT_SECRET no configurado en .env — establece una cadena aleatoria larga "
        "antes de arrancar la API (ver .env.example)."
    )
ALGORITHM     = "HS256"
TOKEN_MINUTES = int(os.getenv("JWT_MINUTES", "480"))  # 8 horas

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


# ---------------------------------------------------------------------------
# Crear y verificar tokens
# ---------------------------------------------------------------------------

def crear_token(uvus: str, rol: str) -> str:
    expira = datetime.now(timezone.utc) + timedelta(minutes=TOKEN_MINUTES)
    payload = {"sub": uvus, "rol": rol, "exp": expira}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _decodificar_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ---------------------------------------------------------------------------
# Dependencias FastAPI
# ---------------------------------------------------------------------------

def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]) -> Usuario:
    payload = _decodificar_token(token)
    uvus    = payload.get("sub")
    if not uvus:
        raise HTTPException(status_code=401, detail="Token sin usuario")
    with get_session() as session:
        usuario = session.query(Usuario).filter_by(uvus=uvus, activo=True).first()
        if not usuario:
            raise HTTPException(status_code=401, detail="Usuario no encontrado o inactivo")
        session.expunge(usuario)
        return usuario


def require_rol(*roles: str):
    """Factoría de dependencias por rol. Uso: Depends(require_rol('admin', 'revisor'))"""
    def _check(usuario: Annotated[Usuario, Depends(get_current_user)]):
        if usuario.rol not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Se requiere rol: {' o '.join(roles)}"
            )
        return usuario
    return _check


# Dependencias listas para usar en los routers
CurrentUser  = Annotated[Usuario, Depends(get_current_user)]
AdminUser    = Annotated[Usuario, Depends(require_rol("admin"))]
RevisorUser  = Annotated[Usuario, Depends(require_rol("admin", "revisor"))]
RecolectorUser = Annotated[Usuario, Depends(require_rol("admin", "revisor", "recolector"))]