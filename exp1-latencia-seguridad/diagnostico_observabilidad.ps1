# Diagnóstico de Prometheus y Grafana. Escribe todo en resultados\diagnostico_observabilidad.txt
# Uso: powershell -ExecutionPolicy Bypass -File .\diagnostico_observabilidad.ps1
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$salida = "resultados\diagnostico_observabilidad.txt"
"Diagnóstico $(Get-Date -Format s)" | Out-File $salida -Encoding utf8

function Seccion($titulo, $comando) {
    "`n===== $titulo =====" | Out-File $salida -Append -Encoding utf8
    cmd /c "$comando 2>&1" | Out-File $salida -Append -Encoding utf8
}

Seccion "Contenedores del laboratorio" "docker compose --profile observabilidad ps -a"
Seccion "Quién publica el puerto 9090 en Docker" "docker ps --filter publish=9090 --format ""{{.Names}} {{.Image}} {{.Ports}}"""
Seccion "Procesos de Windows escuchando en 9090 y 3000" "netstat -ano | findstr "":9090 :3000"" | findstr LISTENING"
Seccion "Códigos HTTP de Prometheus" "for %u in (/-/ready /graph /targets /api/v1/targets) do @curl.exe -s -o NUL -w ""%u -> %{http_code}\n"" http://localhost:9090%u"
Seccion "Versión de Prometheus" "docker compose --profile observabilidad exec -T prometheus prometheus --version"
Seccion "Configuración que ve Prometheus" "docker compose --profile observabilidad exec -T prometheus cat /etc/prometheus/prometheus.yml"
Seccion "Estado de cada objetivo (api/v1/targets)" "curl.exe -s http://localhost:9090/api/v1/targets"
Seccion "Consulta up" "curl.exe -s ""http://localhost:9090/api/v1/query?query=up"""
Seccion "Prometheus puede leer las métricas del gateway" "docker compose --profile observabilidad exec -T prometheus wget -qO- http://gateway:8000/metrics"
Seccion "Log de Prometheus" "docker compose --profile observabilidad logs --tail 60 prometheus"
Seccion "Log de Grafana" "docker compose --profile observabilidad logs --tail 40 grafana"
Seccion "Log del gateway" "docker compose logs --tail 40 gateway"

# Consulta de prueba a través de Grafana: si falla, aquí queda el mensaje exacto del error.
$cuerpo = '{"from":"now-15m","to":"now","queries":[{"refId":"A","datasource":{"type":"prometheus","uid":"prom"},"expr":"up","range":true,"intervalMs":5000,"maxDataPoints":200},{"refId":"B","datasource":{"type":"prometheus","uid":"prom"},"expr":"histogram_quantile(0.95, sum(rate(authz_decision_seconds_bucket[1m])) by (le)) * 1000","range":true,"intervalMs":5000,"maxDataPoints":200}]}'
$cuerpo | Out-File "resultados\_consulta_grafana.json" -Encoding ascii
Seccion "Fuentes de datos en Grafana" "curl.exe -s http://localhost:3000/api/datasources"
Seccion "Consulta de prueba a traves de Grafana" "curl.exe -s -X POST http://localhost:3000/api/ds/query -H ""Content-Type: application/json"" --data-binary @resultados\_consulta_grafana.json"
Seccion "Dashboards cargados en Grafana" "curl.exe -s ""http://localhost:3000/api/search?query=Experimento"""
Seccion "Hora de Windows frente a la de Docker" "echo Windows: %date% %time% & docker compose --profile observabilidad exec -T prometheus date -u"

Write-Host "Listo: $salida" -ForegroundColor Green
