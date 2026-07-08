#!/usr/bin/env bash
# install.sh — Instalacion automatica en Linux / Raspberry Pi
# Ejecutar desde la raiz del proyecto:
#   chmod +x install.sh && ./install.sh

set -e
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

echo ""
echo "=== Instalador Dataset TTS Multiaccento Andaluz (Linux) ==="
echo ""

# Detectar arquitectura (ARM = Raspberry Pi)
ARCH=$(uname -m)
IS_ARM=false
if [[ "$ARCH" == "aarch64" || "$ARCH" == "armv7l" ]]; then
    IS_ARM=true
    echo "[INFO] Arquitectura ARM detectada ($ARCH) — modo Raspberry Pi"
fi

# 1. Dependencias del sistema (incluye PostgreSQL, ffmpeg, espeak-ng)
echo "[...] Instalando dependencias del sistema..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3 python3-pip python3-venv python3-dev \
    ffmpeg espeak-ng \
    postgresql postgresql-contrib libpq-dev gcc build-essential \
    git curl openssl
echo "[OK] Dependencias del sistema instaladas"

# 2. Asegurar que PostgreSQL está activo
echo "[...] Arrancando servicio de PostgreSQL..."
sudo systemctl enable --now postgresql 2>/dev/null || sudo service postgresql start 2>/dev/null || true
echo "[OK] PostgreSQL activo"

# 3. Crear venv
VENV="$PROJECT_ROOT/venv"
if [ ! -d "$VENV" ]; then
    echo "[...] Creando entorno virtual..."
    python3 -m venv "$VENV"
    echo "[OK] venv creado"
else
    echo "[OK] venv ya existe"
fi

PIP="$VENV/bin/pip"
PYTHON="$VENV/bin/python"

# 4. Actualizar pip
echo "[...] Actualizando pip..."
"$PIP" install --upgrade pip setuptools wheel --quiet

# 5. Dependencias base
echo "[...] Instalando dependencias base..."
"$PIP" install -r "$PROJECT_ROOT/requirements.txt" --quiet
echo "[OK] Dependencias base instaladas"

# 6. PyTorch
echo "[...] Instalando PyTorch..."
if [ "$IS_ARM" = true ]; then
    # Raspberry Pi: usar wheels de PyPI (CPU, ARM)
    "$PIP" install torch torchaudio --quiet
    echo "[OK] PyTorch ARM instalado (puede tardar varios minutos)"
else
    # Linux x86 sin GPU
    "$PIP" install torch==2.7.1+cpu torchaudio==2.7.1+cpu \
        --index-url https://download.pytorch.org/whl/cpu --quiet
    echo "[OK] PyTorch CPU instalado"
fi

# 7. CoquiTTS
PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}{sys.version_info.minor}')")
echo "[...] Instalando TTS (Python $("$PYTHON" --version))..."
if [ "$IS_ARM" = true ]; then
    echo "[AVISO] CoquiTTS/XTTS v2 no es viable en Raspberry Pi (sin GPU, poca RAM)."
    echo "        Se omite la instalacion de TTS. Usa Piper para sintesis en RPi."
elif [ "$PY_VER" -ge "312" ]; then
    "$PIP" install "git+https://github.com/idiap/coqui-ai-TTS" --quiet
    echo "[OK] TTS (fork Idiap) instalado"
else
    "$PIP" install TTS --quiet
    echo "[OK] TTS instalado"
fi

# Piper siempre (funciona bien en ARM)
echo "[...] Instalando Piper..."
"$PIP" install piper-tts piper-phonemize --quiet 2>/dev/null || \
    echo "[AVISO] piper-phonemize no disponible en esta arquitectura — Piper puede no funcionar."

# 8. Generar credenciales aleatorias y crear .env si no existe
DB_PASS_GEN=""
if [ ! -f "$PROJECT_ROOT/.env" ]; then
    echo "[...] Generando credenciales aleatorias..."
    JWT_SECRET_GEN=$(openssl rand -hex 32)
    DB_PASS_GEN=$(openssl rand -hex 16)
    cat > "$PROJECT_ROOT/.env" << EOF
DB_HOST=localhost
DB_PORT=5432
DB_NAME=corpus_tts
DB_USER=postgres
DB_PASS=$DB_PASS_GEN
JWT_SECRET=$JWT_SECRET_GEN
JWT_MINUTES=480
HF_HUB_DISABLE_SYMLINKS_WARNING=1
EOF
    echo "[OK] .env creado con JWT_SECRET y DB_PASS generados automáticamente"
else
    echo "[OK] .env ya existe"
    DB_PASS_GEN=$(grep -oP '(?<=^DB_PASS=).*' "$PROJECT_ROOT/.env" || true)
fi

# 9. Crear rol y base de datos PostgreSQL (usa peer auth local vía sudo -u postgres)
if [ -n "$DB_PASS_GEN" ]; then
    echo "[...] Configurando el rol 'postgres' y la base de datos 'corpus_tts'..."
    sudo -u postgres psql -c "ALTER USER postgres PASSWORD '$DB_PASS_GEN';" >/dev/null 2>&1 || \
        echo "[AVISO] No se pudo fijar la contraseña del rol postgres."
    DB_EXISTS=$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='corpus_tts';" 2>/dev/null || true)
    if [ "$DB_EXISTS" = "1" ]; then
        echo "[OK] La base de datos 'corpus_tts' ya existe"
    else
        sudo -u postgres createdb corpus_tts 2>/dev/null && echo "[OK] Base de datos 'corpus_tts' creada" || \
            echo "[AVISO] No se pudo crear la base de datos automáticamente. Créala con: sudo -u postgres createdb corpus_tts"
    fi
else
    echo "[AVISO] .env ya existía con un DB_PASS propio; no se toca la configuración de PostgreSQL."
    echo "        Asegúrate de que la base de datos 'corpus_tts' exista y de que la contraseña coincida."
fi

# 10. Hacer ejecutables los scripts de arranque
chmod +x "$PROJECT_ROOT/start_gui.sh" 2>/dev/null || true
chmod +x "$PROJECT_ROOT/start_api.sh" 2>/dev/null || true

# 11. Inicializar BD (tablas)
echo "[...] Inicializando esquema de la base de datos..."
DB_READY=false
if "$PYTHON" -c "from data.db import init_db; init_db()" 2>/dev/null; then
    echo "[OK] Tablas creadas"
    DB_READY=true
else
    echo "[AVISO] No se pudo conectar a PostgreSQL. Revisa .env y ejecuta cuando esté listo:"
    echo "        $PYTHON -c \"from data.db import init_db; init_db()\""
fi

# 12. Crear el primer usuario administrador (opcional, interactivo)
if [ "$DB_READY" = true ]; then
    echo ""
    read -rp "¿Crear ahora el primer usuario administrador? (s/N) " CREAR_ADMIN
    if [[ "$CREAR_ADMIN" =~ ^[sS]$ ]]; then
        read -rp "  Identificador (uvus): " ADMIN_UVUS
        read -rp "  Nombre completo: " ADMIN_NOMBRE
        read -rsp "  Contraseña: " ADMIN_PASS
        echo ""
        if "$PYTHON" -c "from data.db import crear_usuario; crear_usuario('$ADMIN_UVUS', '$ADMIN_NOMBRE', 'admin', '$ADMIN_PASS')" 2>/dev/null; then
            echo "[OK] Usuario administrador '$ADMIN_UVUS' creado"
        else
            echo "[AVISO] No se pudo crear el usuario. Puedes intentarlo luego con:"
            echo "        $PYTHON -c \"from data.db import crear_usuario; crear_usuario('uvus','Nombre','admin','contraseña')\""
        fi
    fi
fi

echo ""
echo "=== Instalacion completada ==="
echo ""
echo "Proximos pasos:"
if [ "$DB_READY" != true ]; then
    echo "  - Revisa .env y la conexión a PostgreSQL"
fi
echo "  1. Arranca la API:  ./start_api.sh"
echo "  2. Arranca la GUI:  ./start_gui.sh"
if [ "$IS_ARM" = true ]; then
    echo ""
    echo "  [RPi] Usa modelo Whisper 'small' o 'base' (large-v3 es demasiado pesado)"
    echo "  [RPi] Usa Piper para sintesis TTS (XTTS no es viable sin GPU)"
fi
