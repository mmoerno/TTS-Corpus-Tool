#!/usr/bin/env python3
"""
Procesador de audio/vídeo para dataset TTS multiaccento andaluz.
────────────────────────────────────────────────────────────────
Uso:
    python procesar_audios_andalucia.py

El script pregunta interactivamente la carpeta de entrada, provincia,
municipio, ID de hablante y modelo Whisper. Las rutas de salida y el
CSV del NGA están configurados en las constantes de la seccion RUTAS.
"""

import sys
import csv
import re
import random
import subprocess
import unicodedata
from pathlib import Path
from collections import defaultdict
from core.nga import cargar_nga, buscar_municipio_nga, guardar_toponimos_txt, build_whisper_prompt
from core.audio import get_duration_s, convert_direct, segment_by_silence
from core.transcripcion import cargar_modelo_whisper, transcribir
from data.csv_store import (
    load_existing, migrate_csv, split_train_eval,
    append_to_global, LANG, TRAIN_RATIO,
    HEADER_LOCAL, HEADER_GLOBAL
)

from config import (
    NGA_CSV, OUTPUT_ROOT, GLOBAL_CSV, WHISPER_CACHE,
    MAX_DURATION, MIN_DURATION, SILENCE_THRESH, MIN_SILENCE_MS,
    KEEP_SILENCE_MS, TRAIN_RATIO, LANG, WAV_SR,
    WHISPER_MODELOS, WHISPER_DEFAULT, AV_EXTS,
    PROVINCIAS, PROVINCIAS_DISPLAY, HEADER_LOCAL, HEADER_GLOBAL,
)
# ─── Utilidades de texto ──────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    return ''.join(
        c for c in unicodedata.normalize('NFD', text)
        if unicodedata.category(c) != 'Mn'
    ).lower()


# ─── Dependencias ─────────────────────────────────────────────────────────────

def check_dependencies():
    for tool in ["ffmpeg", "ffprobe"]:
        try:
            subprocess.run([tool, "-version"], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            print(f"\n  x '{tool}' no encontrado.")
            print("    Linux:   sudo apt install ffmpeg")
            print("    macOS:   brew install ffmpeg")
            print("    Windows: winget install Gyan.FFmpeg")
            sys.exit(1)
    try:
        import whisper  # noqa: F401
    except ImportError:
        print("\n  x whisper no instalado.  ->  pip install openai-whisper")
        sys.exit(1)
    try:
        from pydub import AudioSegment  # noqa: F401
    except ImportError:
        print("\n  x pydub no instalado.  ->  pip install pydub")
        sys.exit(1)


# ─── NGA ──────────────────────────────────────────────────────────────────────

def _detect_col(fieldnames, candidates):
    """Busca la primera columna que coincida (insensible a mayusculas/espacios)."""
    fn_upper = {f.strip().upper(): f for f in fieldnames}
    for c in candidates:
        if c.strip().upper() in fn_upper:
            return fn_upper[c.strip().upper()]
    return None


def cargar_nga(nga_path: Path):
    """
    Lee el CSV del NGA y devuelve:
      toponimos  : {cod_municipio -> [nombre, ...]}
      municipios : {cod_municipio -> (nombre_oficial, nombre_provincia)}
    """
    if not nga_path.exists():
        print(f"\n  x CSV del NGA no encontrado: {nga_path}")
        print("    Descargalo en: https://www.ieca.junta-andalucia.es/nomenclator/")
        sys.exit(1)

    toponimos  = defaultdict(list)
    municipios = {}
    estados_validos = {
        "normalizado", "oficial",
        "no disponible", "alta", "revision", "revision",
    }

    with open(nga_path, newline="", encoding="utf-8", errors="replace") as f:
        sample = f.read(4096)
        f.seek(0)
        sep = "\t" if sample.count("\t") > sample.count(";") else ";"
        reader = csv.DictReader(f, delimiter=sep)

        # Leer cabecera ANTES de iterar filas
        fieldnames = reader.fieldnames
        if not fieldnames:
            print("  x El CSV del NGA parece estar vacio o mal formateado.")
            sys.exit(1)

        # Columnas reales en NGA_TOPONIMOS_20260309.csv:
        #   T_NOMBRE  N_CODIGO_MUN  V_ESTATUS  T_MUNICIPIO  T_PROVINCIA
        col_nombre   = _detect_col(fieldnames, ["T_NOMBRE",    "TNOMBRE",   "NOMBRE"])
        col_cod_mun  = _detect_col(fieldnames, ["N_CODIGO_MUN","NCODIGOMUN","COD_MUN"])
        col_status   = _detect_col(fieldnames, ["V_ESTATUS",   "VSTATUS",   "STATUS"])
        col_nom_mun  = _detect_col(fieldnames, ["T_MUNICIPIO", "TNOMMUN",   "NOMMUN",  "MUNICIPIO"])
        col_nom_prov = _detect_col(fieldnames, ["T_PROVINCIA", "TNOMPROV",  "NOMPROV", "PROVINCIA"])

        if not col_nombre or not col_cod_mun:
            print("  x No se detectaron columnas de nombre/codigo de municipio en el NGA.")
            print(f"    Columnas encontradas: {', '.join(fieldnames)}")
            sys.exit(1)

        print(f"  . Columnas detectadas -> nombre='{col_nombre}' | "
              f"cod_mun='{col_cod_mun}' | status='{col_status}' | "
              f"municipio='{col_nom_mun}' | provincia='{col_nom_prov}'")

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
                nom_mun  = row.get(col_nom_mun,  "").strip()
                nom_prov = row.get(col_nom_prov, "").strip()
                if nom_mun and nom_prov:
                    municipios[cod] = (nom_mun, nom_prov)

    total_tops = sum(len(v) for v in toponimos.values())
    print(f"  + NGA cargado: {total_tops} toponimos en {len(municipios)} municipios.")
    return dict(toponimos), municipios


def buscar_municipio_nga(municipios: dict, nombre_buscado: str, cod_prov: str):
    """Busca municipios por nombre parcial dentro de la provincia dada."""
    norm = _normalize(nombre_buscado)
    resultados = [
        (cod, nom_mun, nom_prov)
        for cod, (nom_mun, nom_prov) in municipios.items()
        if cod[:2] == cod_prov and norm in _normalize(nom_mun)
    ]
    resultados.sort(key=lambda x: len(x[1]))
    return resultados


# ─── Interaccion con el usuario ───────────────────────────────────────────────

def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"  {prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n\n  Cancelado por el usuario.")
        sys.exit(0)
    return val if val else default


def ask_yn(prompt: str, default: bool = True) -> bool:
    opciones = "S/n" if default else "s/N"
    resp = ask(f"{prompt} ({opciones})")
    if not resp:
        return default
    return resp.lower() in {"s", "si", "si", "y", "yes"}


def elegir_opcion(opciones: list, etiqueta_fn=str) -> int:
    for i, op in enumerate(opciones, 1):
        print(f"    {i}. {etiqueta_fn(op)}")
    while True:
        resp = ask("Elige un numero")
        if resp.isdigit() and 1 <= int(resp) <= len(opciones):
            return int(resp) - 1
        print(f"  x Introduce un numero entre 1 y {len(opciones)}.")


def wizard(municipios: dict):
    """
    Asistente interactivo. Devuelve un dict con todos los parametros,
    o None si el usuario pide reiniciar.
    """
    sep = "-" * 60
    print(f"\n{'='*60}")
    print("  Procesador TTS Multiaccento Andaluz -- dataset XTTS v2")
    print(f"{'='*60}\n")

    # 1. Carpeta de entrada
    while True:
        ruta = ask("Carpeta con los audios/videos de entrada")
        if not ruta:
            print("  x La ruta no puede estar vacia.")
            continue
        p = Path(ruta).expanduser()
        if not p.exists():
            print(f"  x La carpeta '{p}' no existe.")
        elif not p.is_dir():
            print(f"  x '{p}' no es una carpeta.")
        else:
            archivos = [f for f in p.iterdir() if f.suffix.lower() in AV_EXTS]
            if not archivos:
                print(f"  x No se encontraron archivos de audio/video en '{p}'.")
                print(f"    Formatos aceptados: {', '.join(sorted(AV_EXTS))}")
            else:
                print(f"  + {len(archivos)} archivo(s) encontrado(s).")
                input_dir = p
                break
    print()

    # 2. Provincia
    print(f"  {sep}")
    print("  Provincias disponibles:")
    prov_unicas = sorted(PROVINCIAS_DISPLAY.items())
    for cod, nombre in prov_unicas:
        print(f"    {cod} . {nombre}")
    print()

    while True:
        prov_input = ask("Provincia (nombre o codigo de 2 digitos)")
        norm_prov  = _normalize(prov_input)
        if norm_prov in PROVINCIAS:
            cod_prov  = PROVINCIAS[norm_prov]
            provincia = PROVINCIAS_DISPLAY[cod_prov]
            print(f"  + Provincia: {provincia}  (codigo {cod_prov})")
            break
        elif re.match(r"^\d{2}$", prov_input) and prov_input in PROVINCIAS_DISPLAY:
            cod_prov  = prov_input
            provincia = PROVINCIAS_DISPLAY[cod_prov]
            print(f"  + Provincia: {provincia}  (codigo {cod_prov})")
            break
        else:
            print(f"  x Provincia '{prov_input}' no reconocida.")
            print(f"    Validas: {', '.join(n for _, n in prov_unicas)}")
    print()

    # 3. Municipio validado contra el NGA
    print(f"  {sep}")
    while True:
        mun_input = ask("Municipio (nombre, puede ser parcial)")
        if not mun_input.strip():
            print("  x El nombre no puede estar vacio.")
            continue

        resultados = buscar_municipio_nga(municipios, mun_input, cod_prov)

        if not resultados:
            print(f"  x No se encontro ningun municipio con '{mun_input}' en {provincia}.")
            print("    Comprueba la ortografia o escribe un fragmento del nombre.")
            continue

        if len(resultados) == 1:
            cod_municipio, municipio, _ = resultados[0]
            print(f"  + Municipio: {municipio}  (INE {cod_municipio})")
            break

        print(f"  Se encontraron {len(resultados)} coincidencias:")
        idx = elegir_opcion(resultados,
                            etiqueta_fn=lambda r: f"{r[1]}  (INE {r[0]})")
        cod_municipio, municipio, _ = resultados[idx]
        print(f"  + Municipio: {municipio}  (INE {cod_municipio})")
        break
    print()

    # 4. ID de hablante
    print(f"  {sep}")
    while True:
        hab = ask("ID de hablante (2 digitos, ej: 01)")
        if re.match(r"^\d{2}$", hab):
            hablante_id = hab
            print(f"  + Hablante: {hablante_id}")
            break
        print("  x Introduce exactamente 2 digitos (ej: 01, 02, 10).")
    print()

    # 5. Modelo Whisper
    print(f"  {sep}")
    print("  Modelos Whisper disponibles:")
    for i, m in enumerate(WHISPER_MODELOS, 1):
        nota = "  <- recomendado" if m == WHISPER_DEFAULT else ""
        print(f"    {i}. {m}{nota}")
    print()
    while True:
        resp = ask("Modelo Whisper", default=WHISPER_DEFAULT)
        if resp in WHISPER_MODELOS:
            whisper_model = resp
            break
        if resp.isdigit() and 1 <= int(resp) <= len(WHISPER_MODELOS):
            whisper_model = WHISPER_MODELOS[int(resp) - 1]
            break
        print("  x Opcion no valida. Escribe el nombre o el numero de la lista.")
    print(f"  + Modelo: {whisper_model}")
    print()

    # 6. Resumen y confirmacion
    print(f"  {'='*60}")
    print("  RESUMEN")
    print(f"  {'='*60}")
    print(f"  Entrada        : {input_dir}")
    print(f"  Dataset raiz   : {OUTPUT_ROOT}")
    print(f"  Provincia      : {provincia}  (codigo {cod_prov})")
    print(f"  Municipio      : {municipio}  (INE {cod_municipio})")
    print(f"  Hablante ID    : {hablante_id}")
    print(f"  Modelo Whisper : {whisper_model}")
    print(f"  Cache Whisper  : {WHISPER_CACHE}")
    print(f"  NGA CSV        : {NGA_CSV}")
    print(f"  CSV global     : {GLOBAL_CSV}")
    print(f"  {'='*60}\n")

    if not ask_yn("Continuar con estos parametros?", default=True):
        print("  Reiniciando...\n")
        return None

    return {
        "input_dir":     input_dir,
        "provincia":     provincia,
        "municipio":     municipio,
        "cod_municipio": cod_municipio,
        "hablante_id":   hablante_id,
        "whisper_model": whisper_model,
    }


# ─── Audio utils ──────────────────────────────────────────────────────────────

def get_duration_s(path):
    cmd = ["ffprobe", "-v", "error",
           "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def export_wav(seg, out_path):
    (seg.set_frame_rate(WAV_SR)
       .set_channels(1)
       .set_sample_width(2)
       .export(str(out_path), format="wav"))


def convert_direct(src, dst):
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", str(src),
         "-vn", "-ar", str(WAV_SR), "-ac", "1", "-sample_fmt", "s16", str(dst)],
        capture_output=True)
    return r.returncode == 0


def split_by_time(audio, base, out_dir, max_ms):
    parts, start, idx = [], 0, 1
    while start < len(audio):
        chunk = audio[start:start + max_ms]
        if len(chunk) / 1000 >= MIN_DURATION:
            p = out_dir / f"{base}_t{str(idx).zfill(2)}.wav"
            export_wav(chunk, p)
            parts.append(p)
            idx += 1
        start += max_ms
    return parts


def segment_by_silence(src, out_dir, base):
    from pydub import AudioSegment
    from pydub.silence import split_on_silence
    audio  = AudioSegment.from_file(str(src))
    chunks = split_on_silence(
        audio,
        min_silence_len=MIN_SILENCE_MS,
        silence_thresh=SILENCE_THRESH,
        keep_silence=KEEP_SILENCE_MS,
    ) or [audio]
    paths, idx = [], 1
    for chunk in chunks:
        dur = len(chunk) / 1000.0
        if dur < MIN_DURATION:
            continue
        if dur <= MAX_DURATION:
            p = out_dir / f"{base}_seg{str(idx).zfill(2)}.wav"
            export_wav(chunk, p)
            paths.append(p)
            idx += 1
        else:
            sub = split_by_time(chunk, f"{base}_seg{str(idx).zfill(2)}",
                                 out_dir, MAX_DURATION * 1000)
            paths.extend(sub)
            idx += len(sub)
    return paths


# ─── CSV utils ────────────────────────────────────────────────────────────────

def load_existing(csv_path):
    done = set()
    if not Path(csv_path).exists():
        return done
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.reader(f, delimiter="|"):
            if row and row[0] != "audio":
                done.add(row[0])
    return done


def migrate_csv(csv_path, new_header):
    p = Path(csv_path)
    if not p.exists():
        return
    with open(p, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f, delimiter="|"))
    if not rows or len(rows[0]) >= len(new_header):
        return
    print(f"  [migracion] {p.name}: {len(rows[0])} -> {len(new_header)} columnas")
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="|")
        w.writerow(new_header)
        for row in rows[1:]:
            padded = row + [""] * (len(new_header) - len(row))
            w.writerow(padded[:len(new_header)])


def split_train_eval(csv_path, train_path, eval_path, header):
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.reader(f, delimiter="|") if r[0] != "audio"]
    random.seed(42)
    random.shuffle(rows)
    cut = int(len(rows) * TRAIN_RATIO)
    for path, data in [(train_path, rows[:cut]), (eval_path, rows[cut:])]:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter="|")
            w.writerow(header)
            w.writerows(data)
    return len(rows[:cut]), len(rows[cut:])


def append_to_global(global_csv, local_csv, municipio, provincia):
    if not Path(local_csv).exists():
        return
    with open(local_csv, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.reader(f, delimiter="|") if r[0] != "audio"]
    new_rows = [
        [row[0], row[1], row[2] if len(row) > 2 else LANG, municipio, provincia]
        for row in rows
    ]
    global_exists = Path(global_csv).exists()
    with open(global_csv, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="|")
        if not global_exists:
            w.writerow(HEADER_GLOBAL)
        w.writerows(new_rows)


# ─── Toponimos NGA ────────────────────────────────────────────────────────────

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


# ─── Informe ──────────────────────────────────────────────────────────────────

def _fmt(s):
    s = int(s)
    if s < 3600:
        return f"{s//60}m {s%60:02d}s"
    return f"{s//3600}h {(s%3600)//60:02d}m {s%60:02d}s"


def print_report(csv_path, wav_dir, nuevos, omitidos, errores, municipio, provincia):
    stats = defaultdict(lambda: {"clips": 0, "dur": 0.0})
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.reader(f, delimiter="|") if r[0] != "audio"]
    for row in rows:
        wav_name = Path(row[0]).name
        parts    = wav_name.split("_")
        hab      = parts[1] if len(parts) >= 2 else "??"
        full_wav = Path(wav_dir) / wav_name
        dur      = get_duration_s(full_wav) if full_wav.exists() else 0.0
        stats[hab]["clips"] += 1
        stats[hab]["dur"]   += dur
    total_c = sum(v["clips"] for v in stats.values())
    total_d = sum(v["dur"]   for v in stats.values())
    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  INFORME -- {municipio.upper()} ({provincia})")
    print(sep)
    print(f"  {'Hablante':<12} {'Clips':>6} {'Duracion':>10} {'Media/clip':>10}")
    print(f"  {'-'*12} {'-'*6} {'-'*10} {'-'*10}")
    for hab in sorted(stats):
        c = stats[hab]["clips"]; d = stats[hab]["dur"]
        print(f"  {hab:<12} {c:>6} {_fmt(d):>10} {_fmt(d/c if c else 0):>10}")
    print(f"  {'-'*12} {'-'*6} {'-'*10} {'-'*10}")
    print(f"  {'TOTAL':<12} {total_c:>6} {_fmt(total_d):>10}")
    print(sep)
    print(f"  Nuevos transcritos  : {nuevos}")
    print(f"  Omitidos (ya exist) : {omitidos}")
    if errores:
        print(f"  Errores ({len(errores)}):")
        for e in errores[:5]:
            print(f"    . {e}")
    print(sep)
    print("  IMPORTANTE: revisa las transcripciones antes de entrenar.")
    print("  Corrige ceceo, toponimos y elisiones mal transcritas.")
    print(f"{sep}\n")


# ─── Nucleo ───────────────────────────────────────────────────────────────────

def process_directory(params: dict, nga_toponimos: dict):
    import whisper as wmod

    input_dir     = params["input_dir"]
    provincia     = params["provincia"]
    municipio     = params["municipio"]
    cod_municipio = params["cod_municipio"]
    hablante_id   = params["hablante_id"]
    whisper_model = params["whisper_model"]

    mun_dir   = OUTPUT_ROOT / provincia / f"{municipio}_{cod_municipio}"
    wav_dir   = mun_dir / "wavs"
    wav_dir.mkdir(parents=True, exist_ok=True)
    local_csv = mun_dir / "metadata.csv"

    tops_mun = nga_toponimos.get(cod_municipio, [])
    if tops_mun:
        txt_out = mun_dir / f"toponimos_{cod_municipio}.txt"
        guardar_toponimos_txt(tops_mun, txt_out, cod_municipio)
        print(f"  . Toponimos NGA: {len(tops_mun)} (guardados en {txt_out.name})")
    else:
        print(f"  ! Sin toponimos NGA para municipio {cod_municipio}.")

    whisper_prompt = build_whisper_prompt(tops_mun)
    files = sorted(f for f in input_dir.iterdir()
                   if f.suffix.lower() in AV_EXTS)

    migrate_csv(local_csv, HEADER_LOCAL)
    already_done = load_existing(local_csv)

    pending_keys = {
        f"wavs/{cod_municipio}_{hablante_id}_{str(i+1).zfill(2)}.wav"
        for i in range(len(files))
    }
    need_transcribe = bool(pending_keys - already_done)

    if not need_transcribe and already_done:
        print("  . Todos los clips ya estan en el CSV. Whisper no se cargara.")
        model = None
    else:
        if already_done:
            print(f"  . Reanudando -- {len(already_done)} clips ya registrados.")
        print(f"  . {len(files)} archivo(s) a procesar.")
        print(f"  . Cargando modelo Whisper '{whisper_model}' desde {WHISPER_CACHE}...")
        model = wmod.load_model(whisper_model, download_root=str(WHISPER_CACHE))
        print("  . Modelo cargado.")
        if whisper_prompt:
            print(f"  . Prompt toponimos: [{whisper_prompt[:80]}...]")

    csv_new  = not local_csv.exists()
    csv_file = open(local_csv, "a", newline="", encoding="utf-8")
    writer   = csv.writer(csv_file, delimiter="|")
    if csv_new:
        writer.writerow(HEADER_LOCAL)

    errores = []; nuevos = 0; omitidos = 0

    # Calcular el siguiente número de archivo según WAVs ya existentes en wavdir
    _pat = re.compile(rf"^{re.escape(cod_municipio)}_{re.escape(hablante_id)}_([0-9]{{2}})")
    _nums = [int(m.group(1)) for f in wavdir.iterdir() if (m := _pat.match(f.stem))]
    counter = max(_nums, default=0) + 1
    if _nums:
        print(f"  ↪ Numeración continúa desde {counter:02d} ({len(_nums)} WAVs previos detectados).")

    for src in files:
        base = f"{cod_municipio}_{hablante_id}_{str(counter).zfill(2)}"
        dur  = get_duration_s(src)

        if dur == 0.0:
            print(f"\n  [{src.name}] duracion desconocida -- segmentando por precaucion...")
            dur = MAX_DURATION + 1

        if dur > MAX_DURATION:
            print(f"\n  [{src.name}] {dur:.1f}s -- segmentando por silencios...")
            try:
                wavs = segment_by_silence(src, wav_dir, base)
            except Exception as e:
                print(f"    x Error segmentacion: {e}")
                errores.append(str(src)); counter += 1; continue
            if not wavs:
                print("    x Sin segmentos validos tras VAD.")
                errores.append(str(src)); counter += 1; continue
            print(f"    + {len(wavs)} segmentos generados.")
            for _w in wavs:
                print(f"      → Creado: {_w.name}")
        else:
            dst = wav_dir / f"{base}.wav"
            if not dst.exists():
                if not convert_direct(src, dst):
                    print(f"  x Error conversion: {src.name}")
                    errores.append(str(src)); counter += 1; continue
            print(f"      → Creado: {dst.name}")
            wavs = [dst]

        for wav in wavs:
            key = f"wavs/{wav.name}"
            if key in already_done:
                omitidos += 1; continue
            if model is None:
                print(f"  ! {wav.name} sin transcripcion (Whisper no cargado).")
                continue
            print(f"  -> [{wav.name}] transcribiendo...")
            try:
                kwargs = dict(language="es", task="transcribe", fp16=False)
                if whisper_prompt:
                    kwargs["initial_prompt"] = whisper_prompt
                res   = model.transcribe(str(wav), **kwargs)
                texto = res["text"].strip()
                print(f"     '{texto}'")
                writer.writerow([key, texto, LANG])
                csv_file.flush()
                already_done.add(key)
                nuevos += 1
            except Exception as e:
                print(f"  x Error transcripcion: {e}")
                errores.append(str(wav))

        counter += 1

    csv_file.close()

    n_train, n_eval = split_train_eval(
        local_csv,
        mun_dir / "metadata_train.csv",
        mun_dir / "metadata_eval.csv",
        HEADER_LOCAL)
    print(f"  . Split local -> Train: {n_train}  Eval: {n_eval}")

    GLOBAL_CSV.parent.mkdir(parents=True, exist_ok=True)
    append_to_global(GLOBAL_CSV, local_csv, municipio, provincia)
    split_train_eval(
        GLOBAL_CSV,
        GLOBAL_CSV.parent / "metadata_global_train.csv",
        GLOBAL_CSV.parent / "metadata_global_eval.csv",
        HEADER_GLOBAL)
    print(f"  . CSV global actualizado: {GLOBAL_CSV}")
    print_report(local_csv, wav_dir, nuevos, omitidos, errores, municipio, provincia)


# ─── Punto de entrada ─────────────────────────────────────────────────────────

def main():
    check_dependencies()
    print("\n  Cargando NGA...")
    nga_toponimos, municipios_nga = cargar_nga(NGA_CSV)

    params = None
    while params is None:
        params = wizard(municipios_nga)

    process_directory(params, nga_toponimos)


if __name__ == "__main__":
    main()
