"""
data/migrar_nga.py
Migra topónimos del CSV NGA a la tabla toponimo de PostgreSQL.
Crea municipios y provincias faltantes si no existen.
"""
import csv
from pathlib import Path
from data.db import get_session, Municipio, Provincia, Toponimo, engine
from sqlalchemy.dialects.postgresql import insert

CSV_PATH = Path(__file__).parent.parent / "NGA_TOPONIMOS_20260309.csv"

PROVINCIAS = {
    "04": "Almería",  "11": "Cádiz",    "14": "Córdoba",
    "18": "Granada",  "21": "Huelva",   "23": "Jaén",
    "29": "Málaga",   "41": "Sevilla",
}

def migrar():
    print("Leyendo CSV NGA...")
    # Estructura: cod_mun -> {nombre, cod_prov, toponimos[]}
    datos: dict[str, dict] = {}

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter=";")
        next(reader)  # cabecera
        for row in reader:
            if len(row) < 17:
                continue
            nombre_top  = row[1].strip().strip('"')
            cod_prov    = row[13].strip().strip('"').zfill(2)
            cod_mun     = row[15].strip().strip('"').zfill(5)
            nombre_mun  = row[16].strip().strip('"')
            if not cod_mun or not nombre_top:
                continue
            if cod_mun not in datos:
                datos[cod_mun] = {
                    "nombre": nombre_mun,
                    "cod_prov": cod_prov,
                    "toponimos": set(),
                }
            datos[cod_mun]["toponimos"].add(nombre_top)

    print(f"CSV leído: {len(datos)} municipios, "
          f"{sum(len(v['toponimos']) for v in datos.values())} topónimos únicos")

    with get_session() as s:

        # 1. Asegurar provincias
        prov_map: dict[str, int] = {}
        for cod, nombre in PROVINCIAS.items():
            p = s.query(Provincia).filter_by(codigo=cod).first()
            if not p:
                p = Provincia(codigo=cod, nombre=nombre)
                s.add(p)
                s.flush()
                print(f"  + Provincia creada: {nombre}")
            prov_map[cod] = p.id

        # 2. Asegurar municipios
        mun_map: dict[str, int] = {}
        for cod_mun, info in datos.items():
            cod_prov = info["cod_prov"]
            if cod_prov not in prov_map:
                continue
            m = s.query(Municipio).filter_by(codigo_ine=cod_mun).first()
            if not m:
                m = Municipio(
                    codigo_ine=cod_mun,
                    nombre=info["nombre"],
                    provincia_id=prov_map[cod_prov],
                )
                s.add(m)
                s.flush()
            mun_map[cod_mun] = m.id

        print(f"Municipios en BD tras migración: {len(mun_map)}")

        # 3. Insertar topónimos (ignorar duplicados)
        total = 0
        for cod_mun, info in datos.items():
            mun_id = mun_map.get(cod_mun)
            if not mun_id:
                continue
            for nombre in info["toponimos"]:
                existe = s.query(Toponimo).filter_by(
                    municipio_id=mun_id, nombre=nombre
                ).first()
                if not existe:
                    s.add(Toponimo(municipio_id=mun_id, nombre=nombre))
                    total += 1
            s.flush()

        print(f"Topónimos insertados: {total}")

    print("Migración completada.")


if __name__ == "__main__":
    migrar()