import csv
import shutil
from pathlib import Path
from typing import List, Optional

from config import OUTPUT_ROOT, GLOBAL_CSV, HEADER_LOCAL, EXPORT_ROOT
from data.csv_store import split_train_eval


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_csv_rows(csv_path: Path) -> List[List[str]]:
    if not Path(csv_path).exists():
        return []
    with open(csv_path, newline="", encoding="utf-8") as f:
        return [r for r in csv.reader(f, delimiter="|") if r and r[0] != "audio"]


def _ensure_wavs_and_copy(rows, out_wavs: Path, copy_mode: str = "copy"):
    out_wavs.mkdir(parents=True, exist_ok=True)
    results = []
    for row in rows:
        key = row[0]
        filename = Path(key).name
        matches = list(OUTPUT_ROOT.rglob(filename))
        if not matches:
            continue
        src = matches[0]
        dst = out_wavs / filename
        if copy_mode == "symlink":
            try:
                if dst.exists() or dst.is_symlink():
                    dst.unlink()
                dst.symlink_to(src.resolve())
            except Exception:
                # Symlinks require elevated permissions on Windows; fall back to copy
                if not dst.exists():
                    shutil.copy2(src, dst)
        elif copy_mode == "copy":
            if not dst.exists():
                shutil.copy2(src, dst)
        # "reference" → no copia física, solo registra
        results.append((str(key), filename))
    return results


def _get_trans(rows: List[List[str]], src_rel: str) -> str:
    return next((r[1] for r in rows if r[0] == src_rel and len(r) > 1), "")


def _enrich_rows_from_db(rows: List[List[str]]) -> List[List[str]]:
    """
    Sustituye las transcripciones del CSV por las corregidas en la BD
    y elimina los clips marcados activo=False.
    Si la BD no está disponible devuelve las filas originales sin error.
    """
    try:
        from data.db import get_session, Clip as ClipModel
        nombres = [r[0] for r in rows]
        with get_session() as session:
            clips = (
                session.query(ClipModel)
                .filter(ClipModel.nombre_archivo.in_(nombres))
                .all()
            )
            inactivos = {c.nombre_archivo for c in clips if not c.activo}
            trans_map = {
                c.nombre_archivo: c.transcripcion
                for c in clips
                if c.activo and c.transcripcion
            }
        enriched = []
        for r in rows:
            if r[0] in inactivos:
                continue
            new_r = list(r)
            if r[0] in trans_map:
                new_r[1] = trans_map[r[0]]
            enriched.append(new_r)
        return enriched
    except Exception:
        return rows


# ---------------------------------------------------------------------------
# LJSpeech  →  stem|raw_text|normalized_text   (3 cols, sin cabecera)
#
# El formatter ljspeech de CoquiTTS construye la ruta como:
#   {root_path}/wavs/{cols[0]}.wav
# → la col 0 debe ser solo el stem, sin prefijo "wavs/" ni extensión.
# ---------------------------------------------------------------------------

def export_ljspeech(rows: List[List[str]], out_dir: Path, copy_mode: str = "copy"):
    fmt_dir = out_dir / "ljspeech"
    copied = _ensure_wavs_and_copy(rows, fmt_dir / "wavs", copy_mode=copy_mode)
    meta = fmt_dir / "metadata.csv"
    with open(meta, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="|", quoting=csv.QUOTE_NONE, escapechar="\\")
        for src_rel, dst_name in copied:
            trans = _get_trans(rows, src_rel)
            stem = Path(dst_name).stem
            w.writerow([stem, trans, trans])


# ---------------------------------------------------------------------------
# XTTS v2   →  mismo formato que LJSpeech (stem|raw|norm)
#              + splits train/eval SIN cabecera (el loader de CoquiTTS no
#                tiene lógica para saltarse cabeceras en LJSpeech)
# ---------------------------------------------------------------------------

def export_xtts(rows: List[List[str]], out_dir: Path, copy_mode: str = "copy"):
    fmt_dir = out_dir / "xtts"
    fmt_dir.mkdir(parents=True, exist_ok=True)
    _ensure_wavs_and_copy(rows, fmt_dir / "wavs", copy_mode=copy_mode)

    combined = fmt_dir / "metadata.csv"
    with open(combined, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="|", quoting=csv.QUOTE_NONE, escapechar="\\")
        for row in rows:
            src_rel = row[0]
            trans   = row[1] if len(row) > 1 else ""
            stem    = Path(Path(src_rel).name).stem
            w.writerow([stem, trans, trans])

    train_p = fmt_dir / "metadata_train.csv"
    eval_p  = fmt_dir / "metadata_eval.csv"
    # write_header=False: el formatter LJSpeech de CoquiTTS no salta cabeceras
    split_train_eval(combined, train_p, eval_p, [], write_header=False)


# ---------------------------------------------------------------------------
# Piper     →  wavs/filename.wav|speaker_id|text
#
# Formato compatible con piper_train.preprocess.
# El speaker_id se extrae del campo hablante del nombre de archivo.
# NOTA: piper_train requiere fonemización previa con piper_train.preprocess
#       antes de llamar al entrenador.
# ---------------------------------------------------------------------------

def export_piper(rows: List[List[str]], out_dir: Path, copy_mode: str = "copy"):
    fmt_dir = out_dir / "piper"
    copied = _ensure_wavs_and_copy(rows, fmt_dir / "wavs", copy_mode=copy_mode)
    meta = fmt_dir / "metadata.csv"
    with open(meta, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="|", quoting=csv.QUOTE_NONE, escapechar="\\")
        for src_rel, dst_name in copied:
            trans = _get_trans(rows, src_rel)
            stem  = Path(dst_name).stem
            parts = stem.split("_")
            speaker_id = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0
            w.writerow([f"wavs/{dst_name}", speaker_id, trans])


# ---------------------------------------------------------------------------
# F5-TTS    →  /ruta/absoluta/wavs/filename.wav|text
#
# F5-TTS necesita rutas absolutas al audio en su CSV de entrenamiento.
# ---------------------------------------------------------------------------

def export_f5(rows: List[List[str]], out_dir: Path, copy_mode: str = "copy"):
    fmt_dir = out_dir / "f5"
    copied = _ensure_wavs_and_copy(rows, fmt_dir / "wavs", copy_mode=copy_mode)
    meta = fmt_dir / "metadata.csv"
    wavs_abs = (fmt_dir / "wavs").resolve()
    with open(meta, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="|", quoting=csv.QUOTE_NONE, escapechar="\\")
        for src_rel, dst_name in copied:
            trans    = _get_trans(rows, src_rel)
            abs_path = str(wavs_abs / dst_name)
            w.writerow([abs_path, trans])


# ---------------------------------------------------------------------------
# CommonVoice  →  TSV con cabecera de 8 columnas estándar
# ---------------------------------------------------------------------------

CV_HEADER = ["client_id", "path", "sentence", "up_votes", "down_votes", "age", "gender", "accent"]

def _write_cv_tsv(rows_cv: List[List[str]], path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(CV_HEADER)
        w.writerows(rows_cv)


def export_commonvoice(rows: List[List[str]], out_dir: Path, copy_mode: str = "copy"):
    fmt_dir = out_dir / "commonvoice"
    copied = _ensure_wavs_and_copy(rows, fmt_dir / "wavs", copy_mode=copy_mode)

    cv_rows = []
    for src_rel, dst_name in copied:
        trans = _get_trans(rows, src_rel)
        stem  = Path(dst_name).stem
        parts = stem.split("_")
        client_id = "_".join(parts[:2]) if len(parts) >= 2 else stem
        cv_rows.append([
            client_id,
            f"wavs/{dst_name}",
            trans,
            0, 0, "", "", ""
        ])

    n     = len(cv_rows)
    t_end = int(n * 0.80)
    d_end = int(n * 0.90)

    _write_cv_tsv(cv_rows[:t_end],      fmt_dir / "train.tsv")
    _write_cv_tsv(cv_rows[t_end:d_end], fmt_dir / "dev.tsv")
    _write_cv_tsv(cv_rows[d_end:],      fmt_dir / "test.tsv")
    _write_cv_tsv(cv_rows,              fmt_dir / "validated.tsv")


# ---------------------------------------------------------------------------
# CSV genérico
# ---------------------------------------------------------------------------

def export_csv(rows: List[List[str]], out_dir: Path, **_):
    fmt_dir = out_dir / "csv"
    fmt_dir.mkdir(parents=True, exist_ok=True)
    meta = fmt_dir / "metadata_complete.csv"
    with open(meta, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="|", quoting=csv.QUOTE_NONE, escapechar="\\")
        w.writerow(["audio", "transcripcion", "idioma"])
        for r in rows:
            audio = r[0] if len(r) > 0 else ""
            trans = r[1] if len(r) > 1 else ""
            lang  = r[2] if len(r) > 2 else "es"
            w.writerow([audio, trans, lang])


# ---------------------------------------------------------------------------
# Registro de formatos
# ---------------------------------------------------------------------------

FORMATTERS = {
    "ljspeech":    export_ljspeech,
    "xtts":        export_xtts,
    "piper":       export_piper,
    "f5":          export_f5,
    "commonvoice": export_commonvoice,
    "csv":         export_csv,
}


# ---------------------------------------------------------------------------
# Helpers internos de split
# ---------------------------------------------------------------------------

def _read_csv_rows_split(source_csv: Path, split: str) -> List[List[str]]:
    if split not in ("train", "eval"):
        return _read_csv_rows(source_csv)
    p = Path(source_csv)
    train_p = p.with_name("metadata_train.csv")
    eval_p  = p.with_name("metadata_eval.csv")
    if train_p.exists() and eval_p.exists():
        return _read_csv_rows(train_p if split == "train" else eval_p)
    gp_train = GLOBAL_CSV.parent / "metadata_global_train.csv"
    gp_eval  = GLOBAL_CSV.parent / "metadata_global_eval.csv"
    if gp_train.exists() and gp_eval.exists():
        return _read_csv_rows(gp_train if split == "train" else gp_eval)
    return _read_csv_rows(source_csv)


# ---------------------------------------------------------------------------
# Punto de entrada principal
# ---------------------------------------------------------------------------

def export_dataset(
    source_csv: Path,
    out_base: Path,
    formats: List[str],
    copy_mode: str = "copy",
    split: str = "all",
    hablante_prefix: Optional[str] = None,
):
    rows = _read_csv_rows_split(source_csv, split)

    if hablante_prefix:
        rows = [r for r in rows if r[0].startswith(hablante_prefix)]

    # Sustituir transcripciones del CSV por las corregidas en la BD
    rows = _enrich_rows_from_db(rows)

    out_base.mkdir(parents=True, exist_ok=True)

    for fmt in formats:
        fn = FORMATTERS.get(fmt)
        if not fn:
            continue
        try:
            fn(rows, out_base, copy_mode=copy_mode)
        except TypeError:
            fn(rows, out_base)
        except Exception as e:
            raise RuntimeError(f"[export_dataset] Error en formato '{fmt}': {e}") from e
