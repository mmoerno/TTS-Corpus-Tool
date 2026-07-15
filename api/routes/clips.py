from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, timezone
from pydantic import BaseModel

from data.db import get_db, Clip, Correccion
from api.auth import CurrentUser, RevisorUser

router = APIRouter(prefix="/clips", tags=["clips"])


class ClipCreate(BaseModel):
    nombre_archivo: str
    transcripcion: str
    hablante_id: int
    duracion_s: Optional[float] = None
    split: Optional[str] = None


class ClipUpdate(BaseModel):
    transcripcion: str


@router.post("", status_code=201)
def registrar_clip(
    payload: ClipCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = None,
):
    clip = Clip(
        nombre_archivo=payload.nombre_archivo,
        transcripcion=payload.transcripcion,
        hablante_id=payload.hablante_id,
        duracion_s=payload.duracion_s,
        split=payload.split,
        creado_por_id=current_user.id,
        creado_en=datetime.now(timezone.utc),
        activo=True,
    )
    db.add(clip)
    db.commit()
    db.refresh(clip)
    return {"id": clip.id, "nombre_archivo": clip.nombre_archivo}


@router.get("")
def listar_clips(
    hablante_id: Optional[int] = Query(None),
    split: Optional[str] = Query(None),
    activo: Optional[bool] = Query(True),
    db: Session = Depends(get_db),
    current_user: CurrentUser = None,
):
    q = db.query(Clip)
    if hablante_id is not None:
        q = q.filter(Clip.hablante_id == hablante_id)
    if split is not None:
        q = q.filter(Clip.split == split)
    if activo is not None:
        q = q.filter(Clip.activo == activo)
    # El rol recolector solo puede consultar los clips que él mismo registró;
    # revisor y admin necesitan ver todo el corpus para poder revisarlo.
    if current_user.rol == "recolector":
        q = q.filter(Clip.creado_por_id == current_user.id)
    return [_clip_dict(c) for c in q.order_by(Clip.creado_en.desc()).all()]


@router.get("/{clip_id}")
def detalle_clip(
    clip_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = None,
):
    clip = _get_or_404(db, clip_id)
    if current_user.rol == "recolector" and clip.creado_por_id != current_user.id:
        raise HTTPException(status_code=403, detail="Solo puedes consultar tus propios clips")
    return _clip_dict(clip)


@router.put("/{clip_id}")
def actualizar_transcripcion(
    clip_id: int,
    payload: ClipUpdate,
    db: Session = Depends(get_db),
    current_user: RevisorUser = None,
):
    clip = _get_or_404(db, clip_id)
    db.add(Correccion(
        clip_id=clip_id,
        usuario_id=current_user.id,
        texto_anterior=clip.transcripcion,
        texto_nuevo=payload.transcripcion,
        creado_en=datetime.now(timezone.utc),
    ))
    clip.transcripcion = payload.transcripcion
    db.commit()
    db.refresh(clip)
    return _clip_dict(clip)


@router.delete("/{clip_id}", status_code=204)
def borrar_clip(
    clip_id: int,
    db: Session = Depends(get_db),
    _: RevisorUser = None,
):
    clip = _get_or_404(db, clip_id)
    clip.activo = False
    db.commit()


def _get_or_404(db: Session, clip_id: int) -> Clip:
    clip = db.query(Clip).filter(Clip.id == clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip no encontrado")
    return clip


def _clip_dict(c: Clip) -> dict:
    return {
        "id": c.id,
        "nombre_archivo": c.nombre_archivo,
        "transcripcion": c.transcripcion,
        "hablante_id": c.hablante_id,
        "duracion_s": c.duracion_s,
        "split": c.split,
        "activo": c.activo,
        "creado_por_id": c.creado_por_id,
        "creado_en": c.creado_en.isoformat() if c.creado_en else None,
    }
