#!/usr/bin/env bash
# scripts/db_restore.sh — Restaurar la base de datos desde un fichero SQL
# Uso: ./scripts/db_restore.sh scripts/backup_corpus_tts_YYYYMMDD_HHMMSS.sql
set -e
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"
BACKUP_FILE="$1"

if [ -z "$BACKUP_FILE" ]; then
    echo "Uso: $0 <fichero_backup.sql>"
    echo "Backups disponibles:"
    ls "$PROJECT_ROOT/scripts/"backup_*.sql 2>/dev/null || echo "  (ninguno)"
    exit 1
fi

DB_HOST=$(grep -m1 "^DB_HOST=" "$ENV_FILE" | cut -d= -f2 | tr -d '[:space:]')
DB_PORT=$(grep -m1 "^DB_PORT=" "$ENV_FILE" | cut -d= -f2 | tr -d '[:space:]')
DB_NAME=$(grep -m1 "^DB_NAME=" "$ENV_FILE" | cut -d= -f2 | tr -d '[:space:]')
DB_USER=$(grep -m1 "^DB_USER=" "$ENV_FILE" | cut -d= -f2 | tr -d '[:space:]')
DB_PASS=$(grep -m1 "^DB_PASS=" "$ENV_FILE" | cut -d= -f2 | tr -d '[:space:]')

DB_HOST=${DB_HOST:-localhost}
DB_PORT=${DB_PORT:-5432}
DB_NAME=${DB_NAME:-corpus_tts}
DB_USER=${DB_USER:-postgres}

export PGPASSWORD="$DB_PASS"

echo "[db_restore] Restaurando $BACKUP_FILE -> $DB_NAME"
echo "[AVISO] Esto BORRA y recrea la base de datos. Ctrl+C para cancelar."
sleep 3

psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -c "DROP DATABASE IF EXISTS $DB_NAME;"
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -c "CREATE DATABASE $DB_NAME;"
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -f "$BACKUP_FILE"

echo "[OK] Base de datos restaurada."
