import csv
import io
import random
from pathlib import Path
from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from data.db import get_db, Clip
from api.auth import AdminUser, RevisorUser
from config import TRAIN_RATIO

router = APIRouter(prefix="/splits", tags=["splits"])

CV_HEADER = ["client_id", "path", "sentence", "up_votes", "down_votes", "age", "gender", "accent"]


@router.post("/generar")
def generar_splits(
    train_ratio: float = Query(TRAIN_RATIO, ge=0.5, le=0.95),
    db: Session = Depends(get_db),
    _: AdminUser = None,
):
    """Asigna train/eval aleatoriamente a clips activos sin split."""
    clips_sin_split = (
        db.query(Clip)
        .filter(Clip.activo == True, Clip.split == None)
        .all()
    )
    if not clips_sin_split:
        return {"asignados": 0, "train": 0, "eval": 0}

    random.seed(42)
    random.shuffle(clips_sin_split)
    corte = int(len(clips_sin_split) * train_ratio)

    for c in clips_sin_split[:corte]:
        c.split = "train"
    for c in clips_sin_split[corte:]:
        c.split = "eval"

    db.commit()
    return {
        "asignados": len(clips_sin_split),
        "train": corte,
        "eval": len(clips_sin_split) - corte,
    }


@router.get("/export/{formato}")
def exportar_split(
    formato: str,
    split: str = Query("train", pattern="^(train|eval)$"),
    db: Session = Depends(get_db),
    _: RevisorUser = None,
):
    """
    Exporta clips de un split como fichero descargable.
    Formatos: ljspeech | commonvoice | csv
    """
    if formato not in ("ljspeech", "commonvoice", "csv"):
        raise HTTPException(
            status_code=400,
            detail="Formato no soportado. Usa: ljspeech, commonvoice, csv",
        )

    clips = (
        db.query(Clip)
        .filter(Clip.split == split, Clip.activo == True, Clip.transcripcion != None)
        .order_by(Clip.id)
        .all()
    )

    buf = io.StringIO()

    if formato == "ljspeech":
        for c in clips:
            stem = Path(c.nombre_archivo).stem if "." in c.nombre_archivo else c.nombre_archivo.replace(".wav", "")
            buf.write(f"{stem}|{c.transcripcion}|{c.transcripcion}\n")
        media_type = "text/plain"
        filename = f"metadata_{split}.csv"

    elif formato == "commonvoice":
        writer = csv.writer(buf, delimiter="\t")
        writer.writerow(CV_HEADER)
        for c in clips:
            stem  = c.nombre_archivo.replace(".wav", "").replace("wavs/", "")
            parts = stem.split("_")
            client_id = "_".join(parts[:2]) if len(parts) >= 2 else stem
            writer.writerow([
                client_id,
                c.nombre_archivo,
                c.transcripcion,
                0, 0, "", "", "",
            ])
        media_type = "text/tab-separated-values"
        filename = f"commonvoice_{split}.tsv"

    else:  # csv genérico
        writer = csv.writer(buf)
        writer.writerow(["id", "nombre_archivo", "transcripcion", "hablante_id", "duracion_s", "split"])
        for c in clips:
            writer.writerow([c.id, c.nombre_archivo, c.transcripcion, c.hablante_id, c.duracion_s, split])
        media_type = "text/csv"
        filename = f"clips_{split}.csv"

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
