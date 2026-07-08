#!/usr/bin/env bash
# scripts/db_backup.sh — Exportar la base de datos PostgreSQL a un fichero SQL
# Uso: ./scripts/db_backup.sh
set -e
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"

# Leer variables del .env
DB_HOST=$(grep -m1 "^DB_HOST=" "$ENV_FILE" | cut -d= -f2 | tr -d '[:space:]')
DB_PORT=$(grep -m1 "^DB_PORT=" "$ENV_FILE" | cut -d= -f2 | tr -d '[:space:]')
DB_NAME=$(grep -m1 "^DB_NAME=" "$ENV_FILE" | cut -d= -f2 | tr -d '[:space:]')
DB_USER=$(grep -m1 "^DB_USER=" "$ENV_FILE" | cut -d= -f2 | tr -d '[:space:]')
DB_PASS=$(grep -m1 "^DB_PASS=" "$ENV_FILE" | cut -d= -f2 | tr -d '[:space:]')

DB_HOST=${DB_HOST:-localhost}
DB_PORT=${DB_PORT:-5432}
DB_NAME=${DB_NAME:-corpus_tts}
DB_USER=${DB_USER:-postgres}

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$PROJECT_ROOT/scripts/backup_${DB_NAME}_${TIMESTAMP}.sql"

export PGPASSWORD="$DB_PASS"

echo "[db_backup] Exportando $DB_NAME -> $BACKUP_FILE ..."
pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -F plain -f "$BACKUP_FILE"

SIZE=$(du -sh "$BACKUP_FILE" | cut -f1)
echo "[OK] Backup completado: $BACKUP_FILE ($SIZE)"
