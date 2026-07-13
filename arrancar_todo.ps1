# arrancar_todo.ps1 — Lanza la API y la GUI en dos ventanas de PowerShell separadas
#   powershell -ExecutionPolicy Bypass -File arrancar_todo.ps1


# $PSScriptRoot no se resuelve dentro de un .exe compilado con ps2exe (se
# queda vacio); en ese caso usamos la carpeta del propio ejecutable.
if ($PSScriptRoot) {
    $ProjectRoot = $PSScriptRoot
} else {
    $ProjectRoot = Split-Path -Parent ([System.Diagnostics.Process]::GetCurrentProcess().MainModule.FileName)
}

# Localhost nunca debe pasar por un proxy corporativo: si HTTP_PROXY/HTTPS_PROXY
# estan definidas a nivel de sistema y no se excluye localhost, Gradio falla al
# autocomprobarse nada mas arrancar ("Couldn't start the app... code 500").
# Sin here-string deliberadamente: con final de linea LF (segun
# core.autocrlf de cada maquina) Windows PowerShell 5.1 puede no
# reconocer el cierre "@ y da errores de parseo en cascada.
$ProxyGuard = "`$env:NO_PROXY = 'localhost,127.0.0.1,0.0.0.0'; `$env:no_proxy = 'localhost,127.0.0.1,0.0.0.0'"

Write-Host "Arrancando API en una ventana nueva..."
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "cd `"$ProjectRoot`"; $ProxyGuard; .\venv\Scripts\Activate.ps1; .\start_api.ps1"
)

Start-Sleep -Seconds 3

Write-Host "Arrancando GUI en otra ventana nueva..."
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "cd `"$ProjectRoot`"; $ProxyGuard; .\venv\Scripts\Activate.ps1; .\start_gui.ps1"
)

Write-Host ""
Write-Host "Dos ventanas nuevas deberian haberse abierto: una con la API (puerto 8000)"
Write-Host "y otra con la GUI (puerto 7860, se abre sola en el navegador)."
Write-Host "Cierra esas ventanas para detener cada proceso."
