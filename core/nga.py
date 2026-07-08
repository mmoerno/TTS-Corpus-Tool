import csv
import unicodedata
import sys
from pathlib import Path
from collections import defaultdict


def _normalize(text: str) -> str:
    return ''.join(
        c for c in unicodedata.normalize('NFD', text)
        if unicodedata.category(c) != 'Mn'
    ).lower()


def _detect_col(fieldnames, candidates):
    fn_upper = {f.strip().upper(): f for f in fieldnames}
    for c in candidates:
        if c.strip().upper() in fn_upper:
            return fn_upper[c.strip().upper()]
    return None


def cargar_nga(nga_path: Path):
    if not nga_path.exists():
        print(f"\n x CSV del NGA no encontrado: {nga_path}")
        print("   Descargalo en: https://www.ieca.junta-andalucia.es/nomenclator/")
        sys.exit(1)

    toponimos = defaultdict(list)
    municipios = {}
    estados_validos = {"normalizado", "oficial", "no disponible", "alta", "revision"}

    with open(nga_path, newline="", encoding="utf-8", errors="replace") as f:
        sample = f.read(4096)
        f.seek(0)
        sep = "\t" if sample.count("\t") > sample.count(";") else ";"
        reader = csv.DictReader(f, delimiter=sep)
        fieldnames = reader.fieldnames
        if not fieldnames:
            print(" x El CSV del NGA parece estar vacio o mal formateado.")
            sys.exit(1)

        col_nombre  = _detect_col(fieldnames, ["T_NOMBRE", "TNOMBRE", "NOMBRE"])
        col_cod_mun = _detect_col(fieldnames, ["N_CODIGO_MUN", "NCODIGOMUN", "COD_MUN"])
        col_status  = _detect_col(fieldnames, ["V_ESTATUS", "VSTATUS", "STATUS"])
        col_nom_mun = _detect_col(fieldnames, ["T_MUNICIPIO", "TNOMMUN", "NOMMUN", "MUNICIPIO"])
        col_nom_prov= _detect_col(fieldnames, ["T_PROVINCIA", "TNOMPROV", "NOMPROV", "PROVINCIA"])

        if not col_nombre or not col_cod_mun:
            print(" x No se detectaron columnas de nombre/codigo en el NGA.")
            print(f"   Columnas encontradas: {', '.join(fieldnames)}")
            sys.exit(1)

        for row in reader:
            nombre = row.get(col_nombre, "").strip()
            cod    = str(row.get(col_cod_mun, "")).strip().zfill(5)
            status = _normalize(row.get(col_status, "") if col_status else "")

            if not nombre or not cod:
                continue
            if col_status and status and status not in estados_validos:
                continue
            if nombre not in toponimos[cod]:
                toponimos[cod].append(nombre)
            if cod not in municipios and col_nom_mun and col_nom_prov:
                nom_mun  = row.get(col_nom_mun, "").strip()
                nom_prov = row.get(col_nom_prov, "").strip()
                if nom_mun and nom_prov:
                    municipios[cod] = (nom_mun, nom_prov)

    total_tops = sum(len(v) for v in toponimos.values())
    print(f" + NGA cargado: {total_tops} toponimos en {len(municipios)} municipios.")
    return dict(toponimos), municipios


def buscar_municipio_nga(municipios: dict, nombre_buscado: str, cod_prov: str):
    norm = _normalize(nombre_buscado)
    resultados = [
        (cod, nom_mun, nom_prov)
        for cod, (nom_mun, nom_prov) in municipios.items()
        if cod[:2] == cod_prov and norm in _normalize(nom_mun)
    ]
    resultados.sort(key=lambda x: len(x[1]))
    return resultados


def guardar_toponimos_txt(toponimos: list, out_path: Path, cod_municipio: str):
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"# Toponimos NGA - municipio {cod_municipio}\n")
        f.write(f"# Total: {len(toponimos)}\n\n")
        for t in sorted(toponimos):
            f.write(t + "\n")


def build_whisper_prompt(toponimos: list, max_tokens: int = 200) -> str:
    if not toponimos:
        return ""
    sorted_tops = sorted(set(toponimos), key=lambda x: -len(x))
    parts, total = [], 0
    for t in sorted_tops:
        if total + len(t) + 2 > max_tokens * 5:
            break
        parts.append(t)
        total += len(t) + 2
    return ", ".join(parts) + "."