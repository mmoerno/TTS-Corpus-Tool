# scripts/db_backup.ps1 — Exportar la base de datos PostgreSQL a un fichero SQL
# Uso: .\scripts\db_backup.ps1
# Requiere pg_dump en PATH (incluido con la instalacion de PostgreSQL)

$ProjectRoot = Split-Path $PSScriptRoot -Parent
$EnvFile     = Join-Path $ProjectRoot ".env"

# Leer variables del .env
$cfg = @{}
Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
        $k, $v = $line.Split("=", 2)
        $cfg[$k.Trim()] = $v.Trim()
    }
}

$Host_   = $cfg["DB_HOST"]   ?? "localhost"
$Port    = $cfg["DB_PORT"]   ?? "5432"
$DbName  = $cfg["DB_NAME"]   ?? "corpus_tts"
$User    = $cfg["DB_USER"]   ?? "postgres"
$Pass    = $cfg["DB_PASS"]   ?? ""

$Timestamp  = Get-Date -Format "yyyyMMdd_HHmmss"
$BackupFile = Join-Path $ProjectRoot "scripts\backup_${DbName}_${Timestamp}.sql"

$env:PGPASSWORD = $Pass

Write-Host "[db_backup] Exportando $DbName -> $BackupFile ..."
pg_dump -h $Host_ -p $Port -U $User -d $DbName -F plain -f $BackupFile

if ($LASTEXITCODE -eq 0) {
    $Size = [math]::Round((Get-Item $BackupFile).Length / 1MB, 2)
    Write-Host "[OK] Backup completado: $BackupFile ($Size MB)"
} else {
    Write-Error "[ERROR] pg_dump fallo con codigo $LASTEXITCODE"
}
