# start_api.ps1 — Lanzador de la API FastAPI
# Fija HF_HOME a nivel de proceso antes de arrancar Python.

$ProjectRoot = $PSScriptRoot
$HfCache     = Join-Path $ProjectRoot ".hf_cache"

$env:HF_HOME                          = $HfCache
$env:HUGGINGFACE_HUB_CACHE            = Join-Path $HfCache "hub"
$env:HF_HUB_CACHE                     = Join-Path $HfCache "hub"
$env:HF_HUB_DISABLE_SYMLINKS_WARNING  = "1"

Write-Host "[start_api] HF_HOME = $env:HF_HOME"

$Activate = Join-Path $ProjectRoot "venv\Scripts\Activate.ps1"
if (Test-Path $Activate) { & $Activate }

uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
