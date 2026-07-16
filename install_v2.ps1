# install_v2.ps1 — Instalador reforzado para Windows
# Ejecutar desde la raiz del proyecto con el venv DESACTIVADO:
#   powershell -ExecutionPolicy Bypass -File install_v2.ps1
#
# Pensado para maquinas Windows "limpias" (sin Python, sin Git, con o sin
# proxy corporativo). Si algo aqui falla o no aplica a tu caso, el
# instalador original (install.ps1) sigue disponible como alternativa mas
# simple y sigue funcionando igual que siempre.
#
# No cubre: proxies corporativos con autenticacion (usuario/contraseña) o
# con inspeccion TLS que exija instalar un certificado raiz propio — en
# esos casos hay que configurar `http.proxy`/`--trusted-host` a mano, tal
# como se hizo durante las pruebas de este instalador.

$ErrorActionPreference = "Stop"

# Si se hace doble clic en el .exe compilado, la ventana de consola que abre
# ps2exe se cierra sola en cuanto el proceso termina — con un error o sin el.
# Sin este "trap", un fallo inesperado no capturado por los try/catch de mas
# abajo cerraria la ventana antes de que se pudiera leer el mensaje. exit 1
# dentro del trap termina el proceso de forma predecible tras pausar.
trap {
    Write-Host ""
    Write-Host "[ERROR] Ha ocurrido un error inesperado:"
    Write-Host $_.Exception.Message
    Write-Host ""
    Read-Host "Pulsa Enter para cerrar esta ventana"
    exit 1
}

# Desactivar la barra de progreso de Invoke-WebRequest: en Windows PowerShell
# 5.1, redibujarla en cada bloque recibido ralentiza las descargas grandes de
# forma brutal (hasta 10-50x mas lento). Sin esto, bajar el ZIP de ffmpeg
# (~90 MB) o el instalador de Python puede tardar 15-30 min en vez de 1-2.
$ProgressPreference = 'SilentlyContinue'

# $PSScriptRoot no se resuelve dentro de un .exe compilado con ps2exe (se
# queda vacio); en ese caso usamos la carpeta del propio ejecutable.
if ($PSScriptRoot) {
    $ProjectRoot = $PSScriptRoot
} else {
    $ProjectRoot = Split-Path -Parent ([System.Diagnostics.Process]::GetCurrentProcess().MainModule.FileName)
}

Write-Host ""
Write-Host "=== Instalador reforzado Dataset TTS Multiaccento Andaluz (Windows) ==="
Write-Host ""

# 0. TLS 1.2 y deteccion de proxy corporativo
#    Windows Server 2016 y versiones antiguas no habilitan TLS 1.2 por
#    defecto en llamadas .NET/PowerShell, lo que rompe cualquier descarga
#    HTTPS moderna (GitHub, PyPI, HuggingFace, python.org...).
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

Write-Host "[...] Comprobando proxy del sistema..."
# netsh devuelve un array de lineas; hay que unirlo en una sola cadena para
# que -match rellene $Matches con los grupos de captura correctamente.
$ProxyInfo = (netsh winhttp show proxy 2>$null) -join "`n"
$ProxyUrl = $null
if ($ProxyInfo -match "Servidor(es)? proxy\s*:\s*(\S+)") {
    $ProxyUrl = $Matches[2]
} elseif ($ProxyInfo -match "Proxy Server\(s\)\s*:\s*(\S+)") {
    $ProxyUrl = $Matches[1]
}
if ($ProxyUrl -and $ProxyUrl -ne "(none)" -and $ProxyUrl -ne "Direct access (no proxy server)") {
    if ($ProxyUrl -notmatch "^https?://") { $ProxyUrl = "http://$ProxyUrl" }
    Write-Host "[OK] Proxy detectado: $ProxyUrl"
    $env:HTTP_PROXY  = $ProxyUrl
    $env:HTTPS_PROXY = $ProxyUrl
    git config --global http.proxy  $ProxyUrl 2>$null
    git config --global https.proxy $ProxyUrl 2>$null
} else {
    Write-Host "[OK] Sin proxy detectado (acceso directo a internet)"
}
# Localhost nunca debe pasar por el proxy: si no se excluye, la propia GUI
# de Gradio falla al autocomprobarse a si misma nada mas arrancar.
$env:NO_PROXY = "localhost,127.0.0.1,0.0.0.0"
$env:no_proxy = "localhost,127.0.0.1,0.0.0.0"

function Invoke-DownloadWithFallback {
    param([string]$Uri, [string]$OutFile)
    # System.Net.WebClient.DownloadFile en vez de Invoke-WebRequest: para
    # ficheros grandes (el ZIP de ffmpeg ~90 MB, el instalador de Python) es
    # varias veces mas rapido y, sobre todo, no depende de $ProgressPreference
    # ni del host de progreso que ps2exe implementa por su cuenta dentro del
    # .exe compilado — donde Invoke-WebRequest puede arrastrarse. Si WebClient
    # falla (p. ej. un proxy que exige la pila de Invoke-WebRequest), se
    # reintenta con el metodo antiguo antes de rendirse.
    try {
        $wc = New-Object System.Net.WebClient
        if ($env:HTTPS_PROXY) {
            $wc.Proxy = New-Object System.Net.WebProxy($env:HTTPS_PROXY, $true)
        }
        $wc.DownloadFile($Uri, $OutFile)
    } catch {
        Write-Host "[AVISO] Descarga rapida fallo, reintentando ($Uri)..."
        try {
            Invoke-WebRequest -Uri $Uri -OutFile $OutFile -UseBasicParsing
        } catch {
            Write-Host "[AVISO] Descarga fallida ($Uri): $($_.Exception.Message)"
            throw
        }
    }
}

# 1. Git
$GitCmd = Get-Command git -ErrorAction SilentlyContinue
if (-not $GitCmd) {
    Write-Host "[...] Git no encontrado. Instalando Git for Windows..."
    try {
        $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/git-for-windows/git/releases/latest" -UseBasicParsing
        $asset = $rel.assets | Where-Object { $_.name -like "*64-bit.exe" } | Select-Object -First 1
        $gitInstaller = Join-Path $env:TEMP "GitInstaller.exe"
        Invoke-DownloadWithFallback -Uri $asset.browser_download_url -OutFile $gitInstaller
        Start-Process -FilePath $gitInstaller -ArgumentList "/VERYSILENT /NORESTART" -Wait
        $env:Path += ";C:\Program Files\Git\bin;C:\Program Files\Git\cmd"
        Write-Host "[OK] Git instalado"
    } catch {
        Write-Error "No se pudo instalar Git automaticamente. Instalalo manualmente desde git-scm.com/download/win y vuelve a ejecutar este script."
        Read-Host "Pulsa Enter para cerrar esta ventana"
        exit 1
    }
} else {
    Write-Host "[OK] Git encontrado"
}

# 2. Python 3.10-3.12
$PythonCmd = Get-Command python -ErrorAction SilentlyContinue
$PyVerOk = $false
if ($PythonCmd) {
    $PyVerNum = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
    if ($PyVerNum -and [version]$PyVerNum -ge [version]"3.10" -and [version]$PyVerNum -le [version]"3.12") {
        $PyVerOk = $true
    }
}
if (-not $PyVerOk) {
    Write-Host "[...] Python 3.10-3.12 no encontrado. Instalando Python 3.12 (instalador oficial, no la Store)..."
    $pyInstaller = Join-Path $env:TEMP "python-installer.exe"
    Invoke-DownloadWithFallback -Uri "https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe" -OutFile $pyInstaller
    Start-Process -FilePath $pyInstaller -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_launcher=1" -Wait
    $env:Path += ";C:\Program Files\Python312;C:\Program Files\Python312\Scripts"
    Write-Host "[OK] Python 3.12 instalado"
    Write-Host "[AVISO] Si los pasos siguientes fallan al no encontrar 'python', cierra y reabre PowerShell y vuelve a ejecutar este script."
} else {
    Write-Host "[OK] $(python --version)"
}

# 3. Dependencias externas del sistema (ffmpeg, espeak-ng, psql)
Write-Host ""
Write-Host "[...] Comprobando dependencias externas..."

$FfmpegCmd = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($FfmpegCmd) {
    Write-Host "[OK] ffmpeg encontrado en PATH"
} else {
    Write-Host "[AVISO] ffmpeg no esta instalado. Se descargara e instalara automaticamente"
    Write-Host "        (~90 MB); esto puede tardar VARIOS MINUTOS segun tu conexion."
    Write-Host "        Para evitarlo en el futuro, puedes cancelar ahora (Ctrl+C),"
    Write-Host "        instalar ffmpeg aparte (p. ej. 'winget install Gyan.FFmpeg' o"
    Write-Host "        'choco install ffmpeg') y volver a lanzar el instalador."
    Start-Sleep -Seconds 3
    try {
        $ffZip = Join-Path $env:TEMP "ffmpeg.zip"
        Write-Host "      Descargando ffmpeg..."
        Invoke-DownloadWithFallback -Uri "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile $ffZip
        $ffExtract = Join-Path $env:TEMP "ffmpeg_extract"
        if (Test-Path $ffExtract) { Remove-Item -Recurse -Force $ffExtract }
        Write-Host "      Descomprimiendo..."
        # [IO.Compression.ZipFile]::ExtractToDirectory es bastante mas rapido
        # que Expand-Archive para ZIPs grandes con muchos ficheros.
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::ExtractToDirectory($ffZip, $ffExtract)
        $ffDir = "C:\ffmpeg"
        $bin = Get-ChildItem -Path $ffExtract -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
        New-Item -ItemType Directory -Force -Path $ffDir | Out-Null
        Write-Host "      Instalando en $ffDir..."
        Copy-Item (Join-Path $bin.DirectoryName "*") $ffDir -Recurse -Force
        $env:Path += ";$ffDir"
        [Environment]::SetEnvironmentVariable("Path", "$([Environment]::GetEnvironmentVariable('Path','User'));$ffDir", "User")
        Remove-Item -Recurse -Force $ffExtract -ErrorAction SilentlyContinue
        Remove-Item -Force $ffZip -ErrorAction SilentlyContinue
        Write-Host "[OK] ffmpeg instalado en $ffDir"
    } catch {
        Write-Host "[AVISO] No se pudo instalar ffmpeg automaticamente. Necesario para procesar audios."
        Write-Host "        Descarga manual: https://ffmpeg.org/download.html"
    }
}

$EspeakCmd = Get-Command espeak-ng -ErrorAction SilentlyContinue
if ($EspeakCmd) {
    Write-Host "[OK] espeak-ng encontrado en PATH"
} else {
    Write-Host "[...] espeak-ng no encontrado. Intentando descarga directa..."
    try {
        $esRel = Invoke-RestMethod -Uri "https://api.github.com/repos/espeak-ng/espeak-ng/releases/latest" -UseBasicParsing
        $esAsset = $esRel.assets | Where-Object { $_.name -like "*.msi" } | Select-Object -First 1
        if ($esAsset) {
            $esInstaller = Join-Path $env:TEMP "espeak-ng.msi"
            Invoke-DownloadWithFallback -Uri $esAsset.browser_download_url -OutFile $esInstaller
            Start-Process msiexec.exe -ArgumentList "/i `"$esInstaller`" /quiet /norestart" -Wait
            Write-Host "[OK] espeak-ng instalado"
        } else {
            Write-Host "[AVISO] No se encontro instalador .msi de espeak-ng en la ultima release."
        }
    } catch {
        Write-Host "[AVISO] espeak-ng no instalado automaticamente. Solo hace falta para fine-tuning de Piper."
        Write-Host "        Descarga manual: https://github.com/espeak-ng/espeak-ng/releases"
    }
}

$PsqlCmd = Get-Command psql -ErrorAction SilentlyContinue
if ($PsqlCmd) {
    Write-Host "[OK] psql encontrado en PATH (cliente de PostgreSQL)"
} else {
    Write-Host "[AVISO] psql no encontrado. Se usara SQLite local automaticamente si no hay PostgreSQL disponible."
    Write-Host "        (Para produccion de verdad, instala PostgreSQL 14+ desde postgresql.org)"
}

# 4. Entorno virtual
$VenvPath = Join-Path $ProjectRoot "venv"
if (-not (Test-Path $VenvPath)) {
    Write-Host "[...] Creando entorno virtual..."
    python -m venv $VenvPath
    Write-Host "[OK] venv creado"
} else {
    Write-Host "[OK] venv ya existe"
}
$Python = Join-Path $VenvPath "Scripts\python.exe"

function Install-PipPackage {
    param([string[]]$PipArgs, [string]$Description)
    Write-Host "[...] $Description..."
    $trustedHosts = @("--trusted-host", "pypi.org", "--trusted-host", "files.pythonhosted.org", "--trusted-host", "download.pytorch.org")
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
        & $Python -m pip install @PipArgs @trustedHosts --quiet
    } catch {
        # No dejar el catch vacio: silenciaria el mensaje real de pip
        # (la razon exacta del fallo) sin motivo, justo cuando mas hace falta.
        Write-Host $_.Exception.Message
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Fallo instalando: $Description (pip args: $PipArgs). Revisa el mensaje de pip mas arriba."
        Read-Host "Pulsa Enter para cerrar esta ventana"
        exit 1
    }
    Write-Host "[OK] $Description"
}

# 5. pip + PyTorch CPU (sin pin de version exacta: los pins antiguos dejan de
#    existir en el indice con el tiempo; dejamos que pip resuelva la mas
#    reciente compatible con torch+torchaudio a la vez). PyTorch va ANTES que
#    requirements.txt a proposito: openai-whisper depende de "torch" sin
#    fijar version ni indice, asi que si se instala primero requirements.txt,
#    pip resuelve torch desde PyPI normal (build generico, no el CPU-only de
#    este indice) y luego este paso lo encuentra "ya satisfecho" y solo
#    instala torchaudio, dejando versiones de torch/torchaudio desincronizadas.
Install-PipPackage -PipArgs @("--upgrade", "pip", "setuptools", "wheel") -Description "Actualizando pip"
Install-PipPackage -PipArgs @("torch", "torchaudio", "--index-url", "https://download.pytorch.org/whl/cpu") -Description "PyTorch CPU"

# 6. Dependencias base
Install-PipPackage -PipArgs @("-r", (Join-Path $ProjectRoot "requirements.txt")) -Description "Dependencias base"

# 7. transformers (version fija conocida-compatible) + CoquiTTS (fork Idiap)
Install-PipPackage -PipArgs @("transformers==4.57.6") -Description "transformers"
$PyVerNum = & $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ([version]$PyVerNum -ge [version]"3.12") {
    Install-PipPackage -PipArgs @("git+https://github.com/idiap/coqui-ai-TTS") -Description "TTS (fork Idiap, Python 3.12)"
} else {
    Install-PipPackage -PipArgs @("TTS") -Description "TTS"
}
# El fork de TTS exige huggingface_hub<1.0; requirements.txt pide >=1.2.0
# para el resto de la app. Nos quedamos con la version compatible con TTS,
# que es la que hace falta para fine-tuning.
Install-PipPackage -PipArgs @("huggingface_hub<1.0", "--force-reinstall", "--no-deps") -Description "huggingface_hub (version compatible con TTS)"

# 8. Piper (piper-phonemize no publica wheel para todas las plataformas/
#    versiones de Python; el try/catch evita que ese fallo, no fatal, aborte
#    el resto del script — ver comentario en Install-PipPackage mas arriba)
Write-Host "[...] Instalando Piper..."
try {
    & $Python -m pip install piper-tts piper-phonemize --trusted-host pypi.org --trusted-host files.pythonhosted.org --quiet 2>$null
} catch {}
if ($LASTEXITCODE -eq 0) { Write-Host "[OK] Piper instalado" }
else { Write-Host "[AVISO] piper-phonemize no disponible para esta plataforma - Piper puede no funcionar." }

# 9. .env
$EnvFile = Join-Path $ProjectRoot ".env"
if (-not (Test-Path $EnvFile)) {
    Write-Host "[...] Creando .env..."
    $Chars = (48..57) + (65..90) + (97..122)
    $JwtSecret = -join ((1..48) | ForEach-Object { [char]($Chars | Get-Random) })
    # Sin here-string deliberadamente: con final de linea LF (segun
    # core.autocrlf de cada maquina) Windows PowerShell 5.1 puede no
    # reconocer el cierre "@ y da errores de parseo en cascada.
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
    Write-Host "     Si no tienes PostgreSQL, no hace falta tocar nada mas: caera a SQLite local."
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

# 10. Base de datos (opcional, solo si hay psql)
if ($PsqlCmd) {
    Write-Host ""
    $PgPassword = Read-Host "Contraseña del usuario 'postgres' (Enter para omitir y usar SQLite local)" -AsSecureString
    $PgPasswordPlain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($PgPassword))
    if ($PgPasswordPlain) {
        $env:PGPASSWORD = $PgPasswordPlain
        # try/catch: si psql falla (credenciales invalidas, servidor no
        # arrancado...) y escribe en stderr, PowerShell puede convertirlo en
        # un error terminante que aborta el resto del instalador entero.
        try {
            $Exists = & psql -U postgres -h localhost -tAc "SELECT 1 FROM pg_database WHERE datname='corpus_tts';" 2>$null
        } catch { $Exists = $null }
        if ($Exists -match "1") {
            Write-Host "[OK] La base de datos 'corpus_tts' ya existe"
        } else {
            try {
                & psql -U postgres -h localhost -c "CREATE DATABASE corpus_tts;" 2>$null
            } catch {}
            if ($LASTEXITCODE -eq 0) { Write-Host "[OK] Base de datos 'corpus_tts' creada" }
            else { Write-Host "[AVISO] No se pudo crear la base de datos. Creala manualmente si la necesitas." }
        }
        (Get-Content $EnvFile) -replace '^DB_PASS=.*', "DB_PASS=$PgPasswordPlain" | Set-Content $EnvFile
        Remove-Item Env:\PGPASSWORD
    } else {
        Write-Host "[OK] Sin PostgreSQL: se usara el fallback SQLite local automaticamente."
    }
}

# 11. Esquema de la base de datos (funciona igual con SQLite que con PostgreSQL)
Write-Host ""
Write-Host "[...] Inicializando esquema de la base de datos..."
try {
    & $Python -c "from data.db import init_db; init_db()"
    Write-Host "[OK] Esquema listo"
} catch {
    Write-Host "[AVISO] No se pudo inicializar el esquema de la base de datos. Ejecuta cuando este lista:"
    Write-Host "        venv\Scripts\python.exe -c `"from data.db import init_db; init_db()`""
}

# 11.5. Cargar toponimos NGA (municipios/provincias en BD; sin esto los
#       desplegables de "Procesar audios" quedan vacios y no se puede subir audio)
Write-Host ""
Write-Host "[...] Cargando municipios y toponimos del NGA en la base de datos..."
try {
    & $Python -c "from data.migrar_nga import migrar; migrar()"
    Write-Host "[OK] Municipios y toponimos cargados"
} catch {
    Write-Host "[AVISO] No se pudo cargar el NGA. Sin este paso los desplegables de municipio"
    Write-Host "        quedaran vacios. Ejecutalo manualmente cuando la BD este lista:"
    Write-Host "        venv\Scripts\python.exe -c `"from data.migrar_nga import migrar; migrar()`""
}

# 12. Usuario administrador
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

Write-Host ""
Write-Host "=== Instalacion completada ==="
Write-Host ""
Write-Host "Para arrancar la API y la GUI en dos ventanas separadas:"
Write-Host "  .\arrancar_todo.ps1"
Write-Host ""
Write-Host "O por separado:"
Write-Host "  .\start_api.ps1"
Write-Host "  .\start_gui.ps1"
Write-Host ""
Read-Host "Pulsa Enter para cerrar esta ventana"
