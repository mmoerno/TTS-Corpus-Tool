"""
api/routes/usuarios.py
Endpoints de gestión de usuarios y autenticación.
"""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from api.auth import crear_token, CurrentUser, AdminUser
from data.db import get_session, Usuario

router = APIRouter(prefix="/auth", tags=["Usuarios"])


# ---------------------------------------------------------------------------
# Schemas Pydantic
# ---------------------------------------------------------------------------

class TokenResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    rol:          str
    nombre:       str


class UsuarioCreate(BaseModel):
    uvus:     str
    nombre:   str
    rol:      str
    password: str


class UsuarioResponse(BaseModel):
    id:            int
    uvus:          str
    nombre:        str
    rol:           str
    activo:        bool
    creado_en:     datetime
    ultimo_acceso: datetime | None

    class Config:
        from_attributes = True


class PasswordChange(BaseModel):
    password_actual: str
    password_nuevo:  str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/login", response_model=TokenResponse)
def login(form: Annotated[OAuth2PasswordRequestForm, Depends()]):
    """Login con UVUS y contraseña. Devuelve JWT."""
    with get_session() as session:
        usuario = session.query(Usuario).filter_by(
            uvus=form.username, activo=True
        ).first()
        if not usuario or not usuario.check_password(form.password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="UVUS o contraseña incorrectos",
            )
        usuario.ultimo_acceso = datetime.now(timezone.utc)
        token = crear_token(usuario.uvus, usuario.rol)
        return TokenResponse(
            access_token=token,
            rol=usuario.rol,
            nombre=usuario.nombre,
        )


@router.get("/me", response_model=UsuarioResponse)
def get_me(usuario: CurrentUser):
    """Devuelve el perfil del usuario autenticado."""
    return usuario


@router.post("/usuarios", response_model=UsuarioResponse, status_code=201)
def crear_usuario(data: UsuarioCreate, _: AdminUser):
    """Crea un nuevo usuario. Solo administradores."""
    with get_session() as session:
        existe = session.query(Usuario).filter_by(uvus=data.uvus).first()
        if existe:
            raise HTTPException(status_code=409, detail="UVUS ya registrado")
        if data.rol not in ("recolector", "revisor", "admin"):
            raise HTTPException(status_code=400, detail="Rol no válido")
        u = Usuario(uvus=data.uvus, nombre=data.nombre, rol=data.rol)
        u.set_password(data.password)
        session.add(u)
        session.flush()
        session.expunge(u)
        return u


@router.get("/usuarios", response_model=list[UsuarioResponse])
def listar_usuarios(_: AdminUser):
    """Lista todos los usuarios. Solo administradores."""
    with get_session() as session:
        usuarios = session.query(Usuario).order_by(Usuario.creado_en).all()
        for u in usuarios:
            session.expunge(u)
        return usuarios


@router.patch("/usuarios/{uvus}/desactivar", status_code=204)
def desactivar_usuario(uvus: str, _: AdminUser):
    """Desactiva un usuario sin borrarlo."""
    with get_session() as session:
        u = session.query(Usuario).filter_by(uvus=uvus).first()
        if not u:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
        u.activo = False


@router.post("/cambiar-password", status_code=204)
def cambiar_password(data: PasswordChange, usuario: CurrentUser):
    """Cambia la contraseña del usuario autenticado."""
    with get_session() as session:
        u = session.query(Usuario).filter_by(uvus=usuario.uvus).first()
        if not u.check_password(data.password_actual):
            raise HTTPException(status_code=400, detail="Contraseña actual incorrecta")
        u.set_password(data.password_nuevo)