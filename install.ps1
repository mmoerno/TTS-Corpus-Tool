# install.ps1 — Instalacion automatica en Windows
# Ejecutar desde la raiz del proyecto con el venv DESACTIVADO:
#   powershell -ExecutionPolicy Bypass -File install.ps1

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot

Write-Host ""
Write-Host "=== Instalador Dataset TTS Multiaccento Andaluz (Windows) ==="
Write-Host ""

# 1. Verificar Python
$PythonCmd = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $PythonCmd) {
    Write-Error "Python no encontrado. Instala Python 3.10-3.12 desde python.org"
    exit 1
}
$PyVer = python --version 2>&1
Write-Host "[OK] $PyVer"

# 2. Verificar dependencias externas del sistema (ffmpeg, espeak-ng, psql)
Write-Host ""
Write-Host "[...] Comprobando dependencias externas..."

$FfmpegCmd = (Get-Command ffmpeg -ErrorAction SilentlyContinue)
if ($FfmpegCmd) {
    Write-Host "[OK] ffmpeg encontrado en PATH"
} else {
    Write-Host "[AVISO] ffmpeg no encontrado en PATH. Necesario para procesar audios."
    Write-Host "        Instalar con:  choco install ffmpeg   (o descarga manual desde ffmpeg.org)"
}

$EspeakCmd = (Get-Command espeak-ng -ErrorAction SilentlyContinue)
if ($EspeakCmd) {
    Write-Host "[OK] espeak-ng encontrado en PATH"
} else {
    Write-Host "[AVISO] espeak-ng no encontrado en PATH. Necesario solo para fine-tuning de Piper."
    Write-Host "        Instalar con:  choco install espeak   (o winget install eSpeak-NG.eSpeak-NG)"
}

$PsqlCmd = (Get-Command psql -ErrorAction SilentlyContinue)
if ($PsqlCmd) {
    Write-Host "[OK] psql encontrado en PATH (cliente de PostgreSQL)"
} else {
    Write-Host "[AVISO] psql no encontrado en PATH. No se podra crear la base de datos automaticamente."
    Write-Host "        Instala PostgreSQL 14+ desde postgresql.org, o crea la BD manualmente despues:"
    Write-Host "        psql -U postgres -c `"CREATE DATABASE corpus_tts;`""
}

# 3. Crear venv si no existe
$VenvPath = Join-Path $ProjectRoot "venv"
if (-not (Test-Path $VenvPath)) {
    Write-Host "[...] Creando entorno virtual..."
    python -m venv $VenvPath
    Write-Host "[OK] venv creado"
} else {
    Write-Host "[OK] venv ya existe"
}

$Pip    = Join-Path $VenvPath "Scripts\pip.exe"
$Python = Join-Path $VenvPath "Scripts\python.exe"

# 4. Actualizar pip
Write-Host "[...] Actualizando pip..."
& $Pip install --upgrade pip setuptools wheel --quiet

# 5. Dependencias base
Write-Host "[...] Instalando dependencias base..."
& $Pip install -r (Join-Path $ProjectRoot "requirements.txt") --quiet
Write-Host "[OK] Dependencias base instaladas"

# 6. PyTorch CPU
Write-Host "[...] Instalando PyTorch CPU..."
& $Pip install torch==2.7.1+cpu torchaudio==2.7.1+cpu `
    --index-url https://download.pytorch.org/whl/cpu --quiet
Write-Host "[OK] PyTorch instalado"

# 7. transformers + CoquiTTS (fork Idiap para Python 3.12)
Write-Host "[...] Instalando transformers..."
& $Pip install "transformers==4.57.6" --quiet
Write-Host "[OK] transformers instalado"

$PyVerNum = & $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
Write-Host "[...] Instalando TTS (Python $PyVerNum)..."
if ([version]$PyVerNum -ge [version]"3.12") {
    & $Pip install "git+https://github.com/idiap/coqui-ai-TTS" --quiet
} else {
    & $Pip install TTS --quiet
}
Write-Host "[OK] TTS instalado"

# 8. Piper (fine-tuning ligero, recomendado para Raspberry Pi pero instalable aqui tambien)
Write-Host "[...] Instalando Piper..."
& $Pip install piper-tts piper-phonemize --quiet 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "[OK] Piper instalado"
} else {
    Write-Host "[AVISO] piper-phonemize no disponible para esta plataforma — Piper puede no funcionar."
}

# 9. Crear .env si no existe, con JWT_SECRET aleatorio generado automaticamente
$EnvFile = Join-Path $ProjectRoot ".env"
if (-not (Test-Path $EnvFile)) {
    Write-Host "[...] Creando .env..."
    $Chars = (48..57) + (65..90) + (97..122)
    $JwtSecret = -join ((1..48) | ForEach-Object { [char]($Chars | Get-Random) })
    @"
DB_HOST=localhost
DB_PORT=5432
DB_NAME=corpus_tts
DB_USER=postgres
DB_PASS=CAMBIA_ESTO
JWT_SECRET=$JwtSecret
JWT_MINUTES=480
HF_HUB_DISABLE_SYMLINKS_WARNING=1
"@ | Out-File -FilePath $EnvFile -Encoding utf8
    Write-Host "[OK] .env creado con un JWT_SECRET aleatorio ya generado"
    Write-Host "[AVISO] Edita DB_PASS en .env con la contraseña real de tu usuario postgres antes de continuar"
} else {
    Write-Host "[OK] .env ya existe"
}

# 10. Crear la base de datos si no existe (requiere psql y la contraseña de postgres)
if ($PsqlCmd) {
    Write-Host ""
    Write-Host "[...] Comprobando si la base de datos 'corpus_tts' existe..."
    $PgPassword = Read-Host "Contraseña del usuario 'postgres' (Enter para omitir la creación de la BD)" -AsSecureString
    $PgPasswordPlain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($PgPassword))
    if ($PgPasswordPlain) {
        $env:PGPASSWORD = $PgPasswordPlain
        $Exists = & psql -U postgres -h localhost -tAc "SELECT 1 FROM pg_database WHERE datname='corpus_tts';" 2>$null
        if ($Exists -match "1") {
            Write-Host "[OK] La base de datos 'corpus_tts' ya existe"
        } else {
            & psql -U postgres -h localhost -c "CREATE DATABASE corpus_tts;" 2>$null
            if ($LASTEXITCODE -eq 0) {
                Write-Host "[OK] Base de datos 'corpus_tts' creada"
            } else {
                Write-Host "[AVISO] No se pudo crear la base de datos. Créala manualmente:"
                Write-Host "        psql -U postgres -c `"CREATE DATABASE corpus_tts;`""
            }
        }
        # Actualizar DB_PASS en .env para que init_db() pueda conectar a continuación
        (Get-Content $EnvFile) -replace '^DB_PASS=.*', "DB_PASS=$PgPasswordPlain" | Set-Content $EnvFile
        Remove-Item Env:\PGPASSWORD
    } else {
        Write-Host "[AVISO] Creación de la BD omitida. Recuerda crearla manualmente antes de arrancar."
    }
}

# 11. Crear tablas en BD
Write-Host ""
Write-Host "[...] Inicializando esquema de la base de datos..."
try {
    & $Python -c "from data.db import init_db; init_db()"
    Write-Host "[OK] Tablas creadas"
    $DbReady = $true
} catch {
    Write-Host "[AVISO] No se pudo conectar a PostgreSQL. Revisa .env y ejecuta cuando esté listo:"
    Write-Host "        venv\Scripts\python.exe -c `"from data.db import init_db; init_db()`""
    $DbReady = $false
}

# 12. Crear el primer usuario administrador (opcional, interactivo)
if ($DbReady) {
    Write-Host ""
    $CrearAdmin = Read-Host "¿Crear ahora el primer usuario administrador? (s/N)"
    if ($CrearAdmin -eq "s" -or $CrearAdmin -eq "S") {
        $AdminUvus = Read-Host "  Identificador (uvus)"
        $AdminNombre = Read-Host "  Nombre completo"
        $AdminPass = Read-Host "  Contraseña" -AsSecureString
        $AdminPassPlain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
            [Runtime.InteropServices.Marshal]::SecureStringToBSTR($AdminPass))
        try {
            & $Python -c "from data.db import crear_usuario; crear_usuario('$AdminUvus', '$AdminNombre', 'admin', '$AdminPassPlain')"
            Write-Host "[OK] Usuario administrador '$AdminUvus' creado"
        } catch {
            Write-Host "[AVISO] No se pudo crear el usuario. Puedes intentarlo luego con:"
            Write-Host "        venv\Scripts\python.exe -c `"from data.db import crear_usuario; crear_usuario('uvus','Nombre','admin','contraseña')`""
        }
    }
}

Write-Host ""
Write-Host "=== Instalacion completada ==="
Write-Host ""
Write-Host "Proximos pasos:"
if (-not $FfmpegCmd) { Write-Host "  - Instala ffmpeg (ver aviso arriba)" }
if (-not $DbReady)    { Write-Host "  - Revisa .env y crea la base de datos manualmente" }
Write-Host "  1. Arranca la API:  .\start_api.ps1"
Write-Host "  2. Arranca la GUI:  .\start_gui.ps1"
