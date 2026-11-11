# Corre UNA corrida completa del experimento 1, en el orden correcto y sin pasos en paralelo a mano:
#   1) datos limpios  2) k6 en segundo plano  3) ataques durante la carga  4) espera a k6  5) reconciliación
#
# Uso (PowerShell, desde la carpeta del proyecto y con el sistema ya levantado):
#   powershell -ExecutionPolicy Bypass -File .\correr_corrida.ps1 -Nombre corrida1
#   powershell -ExecutionPolicy Bypass -File .\correr_corrida.ps1 -Nombre piloto -VUS 100 -Duracion 1m
param(
    [string]$Nombre = "corrida1",
    [int]$VUS = 1000,
    [string]$Duracion = "10m",
    [int]$EsperaAtaques = 45
)
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$k6 = "exp1-k6-$Nombre"

function Paso($texto) { Write-Host "`n=== $texto ===" -ForegroundColor Cyan }
function Fallo($texto) { Write-Host "ERROR: $texto" -ForegroundColor Red; exit 1 }

Paso "0. Imágenes y sistema arriba (incluidos Prometheus y Grafana)"
docker image inspect exp1-herramientas:latest *> $null
if ($LASTEXITCODE -ne 0) { docker compose --profile herramientas build herramientas }
docker image inspect grafana/k6:0.52.0 *> $null
if ($LASTEXITCODE -ne 0) { docker compose --profile pruebas pull k6 }
docker compose --profile observabilidad up -d
if ($LASTEXITCODE -ne 0) { Fallo "docker compose up falló (¿Docker Desktop está abierto?)" }
$salud = ""
for ($i = 0; $i -lt 30 -and $salud -notmatch "ok"; $i++) {
    $salud = curl.exe -sk https://localhost:8443/salud
    if ($salud -notmatch "ok") { Start-Sleep -Seconds 2 }
}
if ($salud -notmatch "ok") { Fallo "el balanceador no responde en https://localhost:8443/salud. Revisa: docker compose --profile observabilidad ps" }
Write-Host "Sistema arriba: $salud   Grafana: http://localhost:3000"

Paso "1. Datos limpios para '$Nombre' (no ejecutes nada más mientras corre)"
docker compose run --rm herramientas python herramientas/generar_datos.py
if ($LASTEXITCODE -ne 0) { Fallo "generar_datos.py falló" }

Paso "2. Carga legítima con k6 en segundo plano ($VUS VU, rampa 30s + $Duracion)"
$resumenK6 = "resultados\$Nombre.json"
Remove-Item $resumenK6, "resultados\${Nombre}_k6.log", "resultados\${Nombre}_k6_errores.log" -ErrorAction SilentlyContinue
cmd /c "docker rm -f $k6 >nul 2>&1"
# Comprobación previa: k6 debe ver el script y los datos dentro del contenedor.
docker compose run --rm k6 inspect /trabajo/pruebas/k6/guardado.js *> $null
if ($LASTEXITCODE -ne 0) { Fallo "k6 no encuentra /trabajo/pruebas/k6/guardado.js dentro del contenedor. Mueve el proyecto fuera de OneDrive (por ejemplo a C:\exp1) y revisa Docker Desktop > Settings > Resources > File sharing" }
$argumentos = @("compose", "run", "--rm", "--name", $k6, "k6", "run",
    "-e", "VUS=$VUS", "-e", "DURACION=$Duracion",
    "--summary-export", "/trabajo/resultados/$Nombre.json", "/trabajo/pruebas/k6/guardado.js")
$procesoK6 = Start-Process docker -ArgumentList $argumentos -NoNewWindow -PassThru `
    -RedirectStandardOutput "resultados\${Nombre}_k6.log" -RedirectStandardError "resultados\${Nombre}_k6_errores.log"
$null = $procesoK6.Handle   # necesario para leer el código de salida al final

Paso "3. Esperando $EsperaAtaques s a que termine la rampa y lanzando los 120 ataques"
Start-Sleep -Seconds $EsperaAtaques
if ($procesoK6.HasExited) { Fallo "k6 terminó antes de tiempo. Revisa resultados\${Nombre}_k6.log y resultados\${Nombre}_k6_errores.log" }
docker compose run --rm herramientas pytest pruebas/ataques -q --junitxml "resultados/${Nombre}_ataques.xml"

Paso "4. Esperando a que k6 termine (unos minutos; puedes verlo en vivo en http://localhost:3000/d/exp1)"
$procesoK6.WaitForExit()
$codigo = $procesoK6.ExitCode
Write-Host "k6 terminó con código $codigo (0 = umbrales cumplidos, 99 = algún umbral no se cumplió)."
if (-not (Test-Path $resumenK6)) { Fallo "k6 no generó $resumenK6; no se reconcilia con datos viejos. Revisa resultados\${Nombre}_k6_errores.log" }

Paso "5. Reconciliación (las 6 métricas)"
docker compose run --rm herramientas python herramientas/reconciliar.py --k6 "resultados/$Nombre.json" --ataques "resultados/${Nombre}_ataques.xml" --salida "resultados/${Nombre}_resumen.txt"

Write-Host "`nListo. Evidencia de '$Nombre' en resultados\: $Nombre.json, ${Nombre}_ataques.xml, ${Nombre}_k6.log, ${Nombre}_resumen.txt" -ForegroundColor Green
