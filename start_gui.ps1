# start_gui.ps1 — Lanzador de la interfaz Gradio
# Fija HF_HOME a nivel de proceso ANTES de arrancar Python,
# garantizando que huggingface_hub use la caché del proyecto
# independientemente de lo que haya configurado en el sistema.

$ProjectRoot = $PSScriptRoot
$HfCache     = Join-Path $ProjectRoot ".hf_cache"

$env:HF_HOME                          = $HfCache
$env:HUGGINGFACE_HUB_CACHE            = Join-Path $HfCache "hub"
$env:HF_HUB_CACHE                     = Join-Path $HfCache "hub"
$env:HF_HUB_DISABLE_SYMLINKS_WARNING  = "1"   # Windows sin Modo Desarrollador

Write-Host "[start_gui] HF_HOME = $env:HF_HOME"

# Activar venv si existe
$Activate = Join-Path $ProjectRoot "venv\Scripts\Activate.ps1"
if (Test-Path $Activate) { & $Activate }

python (Join-Path $ProjectRoot "gui_tts_andaluz.py")
