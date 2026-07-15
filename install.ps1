# install.ps1 — Instalacion automatica en Windows
# Ejecutar desde la raiz del proyecto con el venv DESACTIVADO:
#   powershell -ExecutionPolicy Bypass -File install.ps1

$ErrorActionPreference = "Stop"

# $PSScriptRoot no se resuelve dentro de un .exe compilado con ps2exe (se
# queda vacio); en ese caso usamos la carpeta del propio ejecutable. Sin
# esto, Instalar.exe crea el venv e instala los paquetes en una ruta
# relativa al directorio de trabajo actual (no siempre el mismo que el del
# .exe), y start_api.ps1/start_gui.ps1 luego activan un venv vacio.
if ($PSScriptRoot) {
    $ProjectRoot = $PSScriptRoot
} else {
    $ProjectRoot = Split-Path -Parent ([System.Diagnostics.Process]::GetCurrentProcess().MainModule.FileName)
}

Write-Host ""
Write-Host "=== Instalador Dataset TTS Multiaccento Andaluz (Windows) ==="
Write-Host ""

# 1. Verificar Python (rango 3.10-3.12: numpy, PyTorch y piper-phonemize solo
#    publican wheels precompiladas para estas versiones. Una version mas nueva
#    (p. ej. 3.13/3.14, la que instala hoy la Microsoft Store) obliga a pip a
#    compilar numpy desde codigo fuente, lo que falla sin un compilador C
#    instalado (MSVC/gcc/clang) y produce errores confusos de "meson" o
#    "vswhere.exe" varios pasos despues de este aviso, en vez de aqui mismo.
$PythonCmd = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $PythonCmd) {
    Write-Error "Python no encontrado. Instala Python 3.10-3.12 desde python.org"
    exit 1
}
$PyVer = python --version 2>&1
$PyVerNum = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
if (-not $PyVerNum -or [version]$PyVerNum -lt [version]"3.10" -or [version]$PyVerNum -gt [version]"3.12") {
    Write-Host ""
    Write-Error "$PyVer no esta soportado. Este proyecto requiere Python 3.10, 3.11 o 3.12 (recomendado: 3.11)."
    Write-Host "        Instala una de esas versiones desde python.org y vuelve a ejecutar este script."
    Write-Host "        Si tienes varias versiones instaladas en paralelo, usa: py -3.11 -m venv venv"
    Write-Host "        (o usa install_v2.ps1, que instala Python 3.12 automaticamente si falta)."
    exit 1
}
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

$Python = Join-Path $VenvPath "Scripts\python.exe"

# Instala un paquete y comprueba el codigo de salida real de pip: sin esto,
# un fallo de compilacion (p. ej. numpy sin wheel para esta version de Python)
# quedaba enmascarado por un "[OK]" que se imprimia igual, y el problema solo
# se descubria minutos despues al arrancar la API/GUI con un modulo faltante.
function Install-PipPackage {
    param([string[]]$PipArgs, [string]$Description)
    Write-Host "[...] $Description..."
    # "python -m pip" y no "pip.exe" directamente: pip no puede sobreescribir
    # su propio .exe mientras se esta ejecutando como tal en Windows, lo que
    # rompe justamente el primer paso (actualizar pip) con "ERROR: To modify
    # pip, please run ... python.exe -m pip install --upgrade pip".
    # try/catch alrededor de la llamada: si pip falla de verdad (exit code
    # distinto de cero) y escribe en stderr, PowerShell (con
    # $ErrorActionPreference = "Stop") lo convierte en un error terminante
    # que aborta TODO el script ahi mismo, saltandose el "if" de abajo y por
    # tanto el mensaje [ERROR] pensado para este caso — y con ello cualquier
    # paso posterior (incluida la creacion de .env) nunca llega a ejecutarse.
    try {
        & $Python -m pip install @PipArgs --quiet
    } catch {}
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Fallo instalando: $Description. Revisa el mensaje de pip mas arriba."
        exit 1
    }
    Write-Host "[OK] $Description"
}

# 4. Actualizar pip
Install-PipPackage -PipArgs @("--upgrade", "pip", "setuptools", "wheel") -Description "Actualizando pip"

# 5. PyTorch CPU (sin pin de version exacta: las versiones antiguas dejan de
#    publicarse en el indice de PyTorch con el tiempo — p. ej. 2.7.1+cpu ya
#    no esta disponible —, asi que dejamos que pip resuelva la mas reciente
#    compatible entre torch y torchaudio). Va ANTES que requirements.txt a
#    proposito: openai-whisper depende de "torch" sin fijar version ni
#    indice, asi que si se instala primero el requirements.txt, pip resuelve
#    torch desde PyPI normal (build generico, no el CPU-only de este indice)
#    y luego este paso lo encuentra "ya satisfecho" y solo instala torchaudio,
#    dejando versiones de torch/torchaudio desincronizadas.
Install-PipPackage -PipArgs @("torch", "torchaudio", "--index-url", "https://download.pytorch.org/whl/cpu") -Description "PyTorch CPU"

# 6. Dependencias base
Install-PipPackage -PipArgs @("-r", (Join-Path $ProjectRoot "requirements.txt")) -Description "Dependencias base"

# 7. transformers + CoquiTTS (fork Idiap para Python 3.12)
Install-PipPackage -PipArgs @("transformers==4.57.6") -Description "transformers"

$PyVerNum = & $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ([version]$PyVerNum -ge [version]"3.12") {
    Install-PipPackage -PipArgs @("git+https://github.com/idiap/coqui-ai-TTS") -Description "TTS (fork Idiap, Python 3.12)"
    # El fork de TTS exige huggingface_hub<1.0; requirements.txt pide >=1.2.0
    # para el resto de la app. Nos quedamos con la version compatible con TTS,
    # que es la que hace falta para poder hacer fine-tuning.
    Install-PipPackage -PipArgs @("huggingface_hub<1.0", "--force-reinstall", "--no-deps") -Description "huggingface_hub (version compatible con TTS)"
} else {
    Install-PipPackage -PipArgs @("TTS") -Description "TTS"
}

# 8. Piper (fine-tuning ligero, recomendado para Raspberry Pi pero instalable
#    aqui tambien; piper-phonemize no publica wheel para todas las
#    plataformas/versiones de Python, el try/catch evita que ese fallo, no
#    fatal, aborte el resto del script)
Write-Host "[...] Instalando Piper..."
try {
    & $Python -m pip install piper-tts piper-phonemize --quiet 2>$null
} catch {}
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
    # Sin here-string deliberadamente: si este fichero se ha extraido de git
    # con final de linea LF (segun la configuracion de core.autocrlf de cada
    # maquina), Windows PowerShell 5.1 puede no reconocer el cierre "@ del
    # here-string y da errores de parseo en cascada en el resto del script.
    $EnvLines = @(
        "DB_HOST=localhost",
        "DB_PORT=5432",
        "DB_NAME=corpus_tts",
        "DB_USER=postgres",
        "DB_PASS=CAMBIA_ESTO",
        "JWT_SECRET=$JwtSecret",
        "JWT_MINUTES=480",
        "HF_HUB_DISABLE_SYMLINKS_WARNING=1"
    )
    $EnvLines | Set-Content -Path $EnvFile -Encoding utf8
    Write-Host "[OK] .env creado con un JWT_SECRET aleatorio ya generado"
    Write-Host "[AVISO] Edita DB_PASS en .env con la contraseña real de tu usuario postgres antes de continuar"
} else {
    Write-Host "[OK] .env ya existe"
    # Un .env de un intento anterior (interrumpido, o creado a mano copiando
    # .env.example) puede no traer JWT_SECRET, o traerlo vacio/con el valor
    # de plantilla: la API rechaza arrancar en ese caso (api/auth.py). Se
    # repara aqui en vez de limitarse a comprobar que el fichero existe.
    $ExistingJwtLine = Get-Content $EnvFile | Where-Object { $_ -match '^JWT_SECRET=(.*)$' } | Select-Object -Last 1
    $ExistingJwtValue = if ($ExistingJwtLine) { $ExistingJwtLine -replace '^JWT_SECRET=', '' } else { "" }
    if ([string]::IsNullOrWhiteSpace($ExistingJwtValue) -or $ExistingJwtValue -match '^(?i)cambia_esto') {
        Write-Host "[...] JWT_SECRET ausente o de plantilla en .env; generando uno nuevo..."
        $Chars = (48..57) + (65..90) + (97..122)
        $JwtSecret = -join ((1..48) | ForEach-Object { [char]($Chars | Get-Random) })
        if (Get-Content $EnvFile | Select-String -Pattern '^JWT_SECRET=' -Quiet) {
            (Get-Content $EnvFile) -replace '^JWT_SECRET=.*', "JWT_SECRET=$JwtSecret" | Set-Content $EnvFile -Encoding utf8
        } else {
            Add-Content -Path $EnvFile -Value "JWT_SECRET=$JwtSecret" -Encoding utf8
        }
        Write-Host "[OK] JWT_SECRET generado y guardado en .env"
    }
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

# 11.5. Cargar topónimos NGA (municipios/provincias en BD; sin esto los
#       desplegables de "Procesar audios" quedan vacíos y no se puede subir audio)
if ($DbReady) {
    Write-Host ""
    Write-Host "[...] Cargando municipios y topónimos del NGA en la base de datos..."
    try {
        & $Python -c "from data.migrar_nga import migrar; migrar()"
        Write-Host "[OK] Municipios y topónimos cargados"
    } catch {
        Write-Host "[AVISO] No se pudo cargar el NGA. Sin este paso los desplegables de municipio"
        Write-Host "        quedarán vacíos. Ejecútalo manualmente cuando la BD esté lista:"
        Write-Host "        venv\Scripts\python.exe -c `"from data.migrar_nga import migrar; migrar()`""
    }
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
