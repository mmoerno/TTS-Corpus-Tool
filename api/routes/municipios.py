from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from typing import Optional

from data.db import get_db, Municipio, Toponimo, Provincia
from api.auth import CurrentUser

router = APIRouter(prefix="/municipios", tags=["municipios"])


@router.get("")
def listar_municipios(
    provincia_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    _: CurrentUser = None,
):
    q = db.query(Municipio)
    if provincia_id is not None:
        q = q.filter(Municipio.provincia_id == provincia_id)
    return [
        {
            "id": m.id,
            "nombre": m.nombre,
            "codigo_ine": m.codigo_ine,
            "provincia_id": m.provincia_id,
            "coordenada_x": m.coordenada_x,
            "coordenada_y": m.coordenada_y,
        }
        for m in q.order_by(Municipio.nombre).all()
    ]


@router.get("/buscar")
def buscar_municipio(
    q: str = Query(..., min_length=2),
    provincia: Optional[str] = Query(None, description="Código INE provincia (ej: 41)"),
    db: Session = Depends(get_db),
    _: CurrentUser = None,
):
    query = db.query(Municipio).filter(Municipio.nombre.ilike(f"%{q}%"))
    if provincia:
        prov = db.query(Provincia).filter(Provincia.codigo == provincia).first()
        if prov:
            query = query.filter(Municipio.provincia_id == prov.id)
    return [
        {"id": m.id, "nombre": m.nombre, "codigo_ine": m.codigo_ine, "provincia_id": m.provincia_id}
        for m in query.order_by(Municipio.nombre).limit(20).all()
    ]


@router.get("/{municipio_id}/toponimos")
def toponimos_de_municipio(
    municipio_id: int,
    db: Session = Depends(get_db),
    _: CurrentUser = None,
):
    municipio = db.query(Municipio).filter(Municipio.id == municipio_id).first()
    if not municipio:
        raise HTTPException(status_code=404, detail="Municipio no encontrado")
    tops = (
        db.query(Toponimo)
        .filter(Toponimo.municipio_id == municipio_id)
        .order_by(Toponimo.nombre)
        .all()
    )
    return {
        "municipio": municipio.nombre,
        "municipio_id": municipio_id,
        "total": len(tops),
        "toponimos": [t.nombre for t in tops],
    }