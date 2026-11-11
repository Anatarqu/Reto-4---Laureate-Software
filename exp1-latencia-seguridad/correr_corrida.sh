#!/usr/bin/env bash
# Corre UNA corrida completa del experimento 1 (macOS / Linux). Igual que correr_corrida.ps1.
#   ./correr_corrida.sh corrida1
#   VUS=100 DURACION=1m ./correr_corrida.sh piloto
set -u
cd "$(dirname "$0")"
NOMBRE="${1:-corrida1}"; VUS="${VUS:-1000}"; DURACION="${DURACION:-10m}"; ESPERA="${ESPERA_ATAQUES:-45}"
K6="exp1-k6-$NOMBRE"
paso() { printf '\n=== %s ===\n' "$1"; }
fallo() { echo "ERROR: $1"; exit 1; }

paso "0. Imágenes y sistema arriba (incluidos Prometheus y Grafana)"
docker image inspect exp1-herramientas:latest >/dev/null 2>&1 || docker compose --profile herramientas build herramientas
docker image inspect grafana/k6:0.52.0 >/dev/null 2>&1 || docker compose --profile pruebas pull k6
docker compose --profile observabilidad up -d || fallo "docker compose up falló"
for i in $(seq 30); do curl -sk https://localhost:8443/salud | grep -q ok && break; sleep 2; done
curl -sk https://localhost:8443/salud | grep -q ok || fallo "el balanceador no responde"
echo "Sistema arriba. Grafana: http://localhost:3000"

paso "1. Datos limpios para '$NOMBRE'"
docker compose run --rm herramientas python herramientas/generar_datos.py || fallo "generar_datos.py falló"

paso "2. k6 en segundo plano ($VUS VU, rampa 30s + $DURACION)"
rm -f "resultados/$NOMBRE.json" "resultados/${NOMBRE}_k6.log"
docker rm -f "$K6" >/dev/null 2>&1
docker compose run --rm k6 inspect /trabajo/pruebas/k6/guardado.js >/dev/null 2>&1 \
  || fallo "k6 no encuentra /trabajo/pruebas/k6/guardado.js dentro del contenedor"
docker compose run --rm --name "$K6" k6 run -e VUS="$VUS" -e DURACION="$DURACION" \
  --summary-export "/trabajo/resultados/$NOMBRE.json" /trabajo/pruebas/k6/guardado.js \
  > "resultados/${NOMBRE}_k6.log" 2>&1 &
PID_K6=$!

paso "3. Esperando $ESPERA s y lanzando los 120 ataques"
sleep "$ESPERA"
kill -0 "$PID_K6" 2>/dev/null || fallo "k6 terminó antes de tiempo; revisa resultados/${NOMBRE}_k6.log"
docker compose run --rm herramientas pytest pruebas/ataques -q --junitxml "resultados/${NOMBRE}_ataques.xml"

paso "4. Esperando a que k6 termine"
wait "$PID_K6"; CODIGO=$?
echo "k6 terminó con código $CODIGO (0 = umbrales cumplidos, 99 = algún umbral no se cumplió)"
[ -f "resultados/$NOMBRE.json" ] || fallo "k6 no generó resultados/$NOMBRE.json; revisa el log"

paso "5. Reconciliación"
docker compose run --rm herramientas python herramientas/reconciliar.py --k6 "resultados/$NOMBRE.json" \
  --ataques "resultados/${NOMBRE}_ataques.xml" --salida "resultados/${NOMBRE}_resumen.txt"
