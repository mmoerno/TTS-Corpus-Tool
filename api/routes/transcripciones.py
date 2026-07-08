from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from data.db import get_db, Clip, Correccion
from api.auth import CurrentUser, RevisorUser

router = APIRouter(prefix="/transcripciones", tags=["transcripciones"])


@router.get("/pendientes")
def clips_pendientes(
    hablante_id: Optional[int] = Query(None),
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
    _: CurrentUser = None,
):
    """Clips activos sin ninguna corrección asociada."""
    q = (
        db.query(Clip)
        .outerjoin(Correccion)
        .filter(Correccion.id == None, Clip.activo == True)
    )
    if hablante_id is not None:
        q = q.filter(Clip.hablante_id == hablante_id)
    clips = q.order_by(Clip.creado_en.asc()).limit(limit).all()
    return [
        {
            "id": c.id,
            "nombre_archivo": c.nombre_archivo,
            "transcripcion": c.transcripcion,
            "hablante_id": c.hablante_id,
            "duracion_s": c.duracion_s,
        }
        for c in clips
    ]


@router.get("/historial/{clip_id}")
def historial_correcciones(
    clip_id: int,
    db: Session = Depends(get_db),
    _: CurrentUser = None,
):
    """Historial completo de correcciones de un clip."""
    correcciones = (
        db.query(Correccion)
        .filter(Correccion.clip_id == clip_id)
        .order_by(Correccion.creado_en.asc())
        .all()
    )
    return [
        {
            "id": c.id,
            "usuario_id": c.usuario_id,
            "texto_anterior": c.texto_anterior,
            "texto_nuevo": c.texto_nuevo,
            "creado_en": c.creado_en.isoformat() if c.creado_en else None,
        }
        for c in correcciones
    ]