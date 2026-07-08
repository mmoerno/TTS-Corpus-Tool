#!/usr/bin/env python3
"""
data/migrate.py
Migra los datos existentes de CSV a PostgreSQL.
Seguro de ejecutar múltiples veces (no duplica datos).

Uso:
    python data/migrate.py
"""

import csv
import re
import random
from pathlib import Path

from data.db import (
    get_session, init_db,
    Provincia, Municipio, Hablante, Clip, Usuario,
)
from config import OUTPUT_ROOT, PROVINCIAS_DISPLAY, HEADER_GLOBAL

# ---------------------------------------------------------------------------
# Usuario sistema para clips migrados (no tiene dueño real)
# ---------------------------------------------------------------------------
SISTEMA_UVUS   = "sistema_migracion"
SISTEMA_NOMBRE = "Migración CSV"
SISTEMA_ROL    = "recolector"
SISTEMA_PASS   = "no_acceso_00"   # cuenta desactivada tras migración

TRAIN_RATIO = 0.85


def _get_or_create_usuario_sistema(session) -> Usuario:
    u = session.query(Usuario).filter_by(uvus=SISTEMA_UVUS).first()
    if not u:
        u = Usuario(uvus=SISTEMA_UVUS, nombre=SISTEMA_NOMBRE, rol=SISTEMA_ROL, activo=False)
        u.set_password(SISTEMA_PASS)
        session.add(u)
        session.flush()
        print(f"  + Usuario sistema creado: {SISTEMA_UVUS}")
    return u


def _get_or_create_provincia(session, nombre: str, codigo: str) -> Provincia:
    p = session.query(Provincia).filter_by(codigo=codigo).first()
    if not p:
        p = Provincia(codigo=codigo, nombre=nombre)
        session.add(p)
        session.flush()
    return p


def _get_or_create_municipio(session, nombre: str, codigo_ine: str, provincia: Provincia) -> Municipio:
    m = session.query(Municipio).filter_by(codigo_ine=codigo_ine).first()
    if not m:
        m = Municipio(
            provincia_id=provincia.id,
            codigo_ine=codigo_ine,
            nombre=nombre,
        )
        session.add(m)
        session.flush()
    return m


def _get_or_create_hablante(session, codigo: str, municipio: Municipio) -> Hablante:
    h = session.query(Hablante).filter_by(
        municipio_id=municipio.id, codigo=codigo
    ).first()
    if not h:
        h = Hablante(municipio_id=municipio.id, codigo=codigo)
        session.add(h)
        session.flush()
    return h


def _asignar_splits(clips_nuevos: list[Clip]):
    """Asigna split train/eval aleatoriamente con ratio 85/15."""
    random.seed(42)
    random.shuffle(clips_nuevos)
    cut = int(len(clips_nuevos) * TRAIN_RATIO)
    for i, clip in enumerate(clips_nuevos):
        clip.split = "train" if i < cut else "eval"


def _cod_prov_desde_nombre(nombre_prov: str) -> str:
    """Obtiene el código de provincia a partir del nombre de la carpeta."""
    norm = nombre_prov.strip().lower()
    # Mapa inverso: nombre -> codigo
    inverso = {v.lower(): k for k, v in PROVINCIAS_DISPLAY.items()}
    return inverso.get(norm, "??")


def migrar():
    init_db()
    total_clips = 0
    total_mun   = 0

    with get_session() as session:
        usuario_sistema = _get_or_create_usuario_sistema(session)

        # Recorre dataset/{Provincia}/{Municipio}_{cod_ine}/metadata.csv
        for meta_path in sorted(OUTPUT_ROOT.rglob("metadata.csv")):
            mun_dir     = meta_path.parent
            folder      = mun_dir.name              # "Utrera_41095"
            prov_nombre = mun_dir.parent.name        # "Sevilla"

            # Extrae nombre municipio y código INE del nombre de carpeta
            parts = folder.rsplit("_", 1)
            if len(parts) != 2:
                print(f"  ! Carpeta no reconocida, omitiendo: {folder}")
                continue
            mun_nombre, cod_ine = parts
            cod_prov = _cod_prov_desde_nombre(prov_nombre)

            if cod_prov == "??":
                print(f"  ! Provincia no reconocida: {prov_nombre}, omitiendo.")
                continue

            print(f"\n → {prov_nombre} / {mun_nombre} ({cod_ine})")

            # Provincia y municipio
            provincia = _get_or_create_provincia(session, prov_nombre, cod_prov)
            municipio = _get_or_create_municipio(session, mun_nombre, cod_ine, provincia)
            total_mun += 1

            # Lee el CSV local
            with open(meta_path, newline="", encoding="utf-8") as f:
                rows = [r for r in csv.reader(f, delimiter="|") if r and r[0] != "audio"]

            clips_nuevos = []
            omitidos     = 0

            for row in rows:
                nombre_archivo = row[0].strip()
                transcripcion  = row[1].strip() if len(row) > 1 else ""
                idioma         = row[2].strip() if len(row) > 2 else "es"

                # Comprueba si ya existe
                existe = session.query(Clip).filter_by(
                    nombre_archivo=nombre_archivo
                ).first()
                if existe:
                    omitidos += 1
                    continue

                # Extrae codigo hablante del nombre de fichero
                # formato: wavs/{cod_ine}_{hablante}_{resto}.wav
                m = re.match(r"wavs/\d+_(\d{2})_", nombre_archivo)
                cod_hablante = m.group(1) if m else "00"

                hablante = _get_or_create_hablante(session, cod_hablante, municipio)

                # Duración desde fichero WAV si existe
                wav_path = mun_dir / nombre_archivo
                duracion = None
                if wav_path.exists():
                    try:
                        from core.audio import get_duration_s
                        duracion = get_duration_s(wav_path)
                    except Exception:
                        pass

                clip = Clip(
                    hablante_id    = hablante.id,
                    creado_por_id  = usuario_sistema.id,
                    nombre_archivo = nombre_archivo,
                    transcripcion  = transcripcion,
                    idioma         = idioma,
                    duracion_s     = duracion,
                    activo         = True,
                )
                session.add(clip)
                clips_nuevos.append(clip)

            # Flush para obtener IDs antes de asignar splits
            session.flush()
            _asignar_splits(clips_nuevos)

            nuevos = len(clips_nuevos)
            total_clips += nuevos
            print(f"   + {nuevos} clips nuevos | {omitidos} ya existían")

    print(f"\n{'='*50}")
    print(f" Migración completada.")
    print(f"   Municipios procesados : {total_mun}")
    print(f"   Clips migrados        : {total_clips}")
    print(f"{'='*50}")


if __name__ == "__main__":
    migrar()
