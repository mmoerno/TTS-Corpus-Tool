from pathlib import Path

# ---------------------------------------------------------------------------
# Rutas principales
# ---------------------------------------------------------------------------
BASE_DIR     = Path(__file__).parent
NGA_CSV      = BASE_DIR / "NGA_TOPONIMOS_20260309.csv"
OUTPUT_ROOT  = BASE_DIR / "dataset"
GLOBAL_CSV   = OUTPUT_ROOT / "metadata_global.csv"
# Caché de modelos Whisper: dentro del proyecto (portable, evita llenar C:\Users\...\.cache).
WHISPER_CACHE = BASE_DIR / ".whisper_cache"
WHISPER_CACHE.mkdir(parents=True, exist_ok=True)

# Caché de modelos HuggingFace: dentro del proyecto para ser portable entre máquinas.
# Se puede sobreescribir con HF_HOME en el .env si se prefiere otra ubicación.
HF_CACHE = BASE_DIR / ".hf_cache"
HF_CACHE.mkdir(parents=True, exist_ok=True)
EXPORT_ROOT  = BASE_DIR / "exports"
EXPORT_ROOT.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Constantes de procesado de audio
# ---------------------------------------------------------------------------
MAX_DURATION    = 15
MIN_DURATION    = 2
SILENCE_THRESH  = -40
MIN_SILENCE_MS  = 400
KEEP_SILENCE_MS = 100
TRAIN_RATIO     = 0.85
LANG            = "es"
WAV_SR          = 22050

# ---------------------------------------------------------------------------
# Whisper
# ---------------------------------------------------------------------------
WHISPER_MODELOS = ["tiny", "base", "small", "medium", "large", "large-v2", "large-v3"]
WHISPER_DEFAULT = "large-v3"

# ---------------------------------------------------------------------------
# Extensiones de audio/video aceptadas
# ---------------------------------------------------------------------------
AV_EXTS = {
    ".ogg", ".opus", ".mp3", ".m4a", ".aac", ".wav", ".flac",
    ".wma", ".amr", ".3gp",
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".ts",
    ".mts", ".m2ts", ".flv", ".ogv",
}

# ---------------------------------------------------------------------------
# Provincias
# ---------------------------------------------------------------------------
PROVINCIAS = {
    "almeria": "04", "almería": "04",
    "cadiz":   "11", "cádiz":   "11",
    "cordoba": "14", "córdoba": "14",
    "granada": "18",
    "huelva":  "21",
    "jaen":    "23", "jaén":    "23",
    "malaga":  "29", "málaga":  "29",
    "sevilla": "41",
}

PROVINCIAS_DISPLAY = {
    "04": "Almeria", "11": "Cadiz",   "14": "Cordoba",
    "18": "Granada", "21": "Huelva",  "23": "Jaen",
    "29": "Malaga",  "41": "Sevilla",
}

# ---------------------------------------------------------------------------
# Cabeceras CSV
# ---------------------------------------------------------------------------
HEADER_LOCAL  = ["audio", "transcripcion", "idioma"]
HEADER_GLOBAL = ["audio", "transcripcion", "idioma", "municipio", "provincia"]