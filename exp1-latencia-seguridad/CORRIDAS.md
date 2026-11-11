## A. Actualizar a esta versión corregida (solo la primera vez)

```powershell
docker compose --profile herramientas build herramientas
docker compose up -d --build
docker compose up -d --force-recreate gateway opa-sidecar
docker compose --profile herramientas build herramientas      
docker compose --profile pruebas pull k6                
```

1. Abre **Docker Desktop** y espera a que diga *Engine running*.

2. Levanta **todo** el laboratorio, incluidos Prometheus y Grafana. Si ya estaba arriba no pasa nada, y no reconstruye nada:

   ```powershell
   docker compose --profile observabilidad up -d
   docker compose --profile observabilidad ps
   curl.exe -sk https://localhost:8443/salud
   ```

   Debes ver `{"ok":true}` y 14 contenedores en `running`: balanceador, gateway, opa-sidecar, idp, 2 de respuestas, persistidor, redis-primario, redis-replica, broker, logs-seguridad, postgres, prometheus y grafana. `postgres`, `redis-primario` y `broker` deben aparecer como `healthy`. Si recién lo levantaste, espera 30 s.

   > **k6 y herramientas no aparecen en esa lista, y es normal.** No son servicios que queden encendidos, sino contenedores de un solo uso: el script los crea durante la corrida (k6 para la carga; herramientas para generar datos, lanzar los ataques y reconciliar) y los borra al terminar. En Docker Desktop sus imágenes aparecen como *In use* solo mientras corre una corrida. Por eso `docker compose up` no los levanta.

5. Abre Grafana en <http://localhost:3000/d/exp1, dashboard **Experimento 1** (entra sin usuario), para ver la corrida en vivo. Prometheus está en <http://localhost:9090> y la consola de RabbitMQ en <  > (guest / guest).

## C. Hacer una corrida

Un solo comando por corrida. El script hace todo en orden:

| Paso | Qué hace |
|---|---|
| 0 | Comprueba las imágenes de k6 y herramientas, levanta todo (incluidos Prometheus y Grafana) y espera a que el sistema responda |
| 1 | `generar_datos.py`: deja la BD y Redis limpios y crea 1.000 estudiantes, sus tokens y los insumos de los ataques |
| 2 | Arranca k6 en segundo plano (1.000 VU: rampa de 30 s y 10 min de carga) |
| 3 | Espera 45 s (fin de la rampa) y lanza los 120 ataques y los 2 controles **durante la carga** |
| 4 | Espera a que k6 termine y guarda su log |
| 5 | Reconcilia y muestra las 6 métricas con CUMPLE o NO CUMPLE |

```powershell
docker images --format "{{.Repository}}:{{.Tag}}" | Select-String "exp1-|k6"
docker compose --profile observabilidad up -d
curl.exe -sk https://localhost:8443/salud
```

```powershell
Remove-Item resultados\corrida1.json -ErrorAction SilentlyContinue
docker compose run --rm herramientas python herramientas/generar_datos.py
```

Para las 3 corridas del diseño, ejecútalas **una detrás de otra** (nunca dos a la vez):

```powershell
powershell -ExecutionPolicy Bypass -File .\correr_corrida.ps1 -Nombre corrida1
powershell -ExecutionPolicy Bypass -File .\correr_corrida.ps1 -Nombre corrida2
powershell -ExecutionPolicy Bypass -File .\correr_corrida.ps1 -Nombre corrida3
```

Parámetros opcionales:

| Parámetro | Por defecto | Para qué |
|---|---|---|
| `-Nombre` | `corrida1` | nombre de los archivos de evidencia |
| `-VUS` | `1000` | estudiantes simultáneos |
| `-Duracion` | `10m` | ventana medida, después de la rampa |
| `-EsperaAtaques` | `45` | segundos antes de lanzar los ataques |

Ejemplo de prueba rápida (≈ 3 min) para confirmar que todo funciona antes de las corridas largas:

```powershell
powershell -ExecutionPolicy Bypass -File .\correr_corrida.ps1 -Nombre piloto -VUS 100 -Duracion 1m
```

## D. Leer el resultado

Al final el script imprime algo así (también queda en `resultados\corridaN_resumen.txt`):

```
=== Resumen de la corrida (para la tabla de resultados) ===
  REND-01  p95 del guardado            25 ms   meta <= 400 ms   CUMPLE
  REND-01  p99 del guardado            52 ms   meta <= 600 ms   CUMPLE
  REND-01  Respuestas perdidas              0   meta 0          CUMPLE
  SEG-01   Ataques bloqueados         100.0 %   meta 100 %      CUMPLE
  SEG-01   p95 de autorización        3.40 ms   meta <= 5 ms    CUMPLE
  SEG-01   Ataques registrados        100.0 %   meta 100 %      CUMPLE
```

Lo que copias a la tabla de resultados es la fila de cada métrica en cada una de las 3 corridas. La hipótesis se acepta si las 6 cumplen en las 3 corridas.

Archivos de evidencia por corrida en `resultados\`:

| Archivo | Contenido |
|---|---|
| `corridaN.json` | resumen de k6 (latencias, guardados confirmados, errores y reintentos) |
| `corridaN_ataques.xml` | resultado de cada uno de los 122 casos de pytest |
| `corridaN_k6.log` | salida completa de k6 |
| `corridaN_resumen.txt` | las 6 métricas con CUMPLE o NO CUMPLE |

**Cómo leer el código de salida de k6** (el script lo muestra): `0` significa que todos los umbrales se cumplieron; `99` significa que al menos uno no se cumplió, y el log dice cuál (aparece con ✗). En el JSON de `--summary-export`, dentro de `thresholds`, `false` significa que el umbral **sí** se cumplió y `true` que se cruzó. Es al revés de lo que parece.

**Errores y reintentos:** el resumen muestra `errores HTTP vistos por k6` y `reintentos del portal`. El portal real reintenta el autoguardado una vez si el servidor no confirmó (5xx), y k6 hace lo mismo. Unos pocos reintentos son normales. Si los errores se cuentan por cientos, revisa *Problemas frecuentes*.

## E. Problemas frecuentes en corridas repetidas

| Síntoma | Causa | Qué hacer |
|---|---|---|
| `ERROR: el balanceador no responde` | Docker Desktop no está abierto o el sistema no terminó de arrancar | abrir Docker Desktop, `docker compose --profile observabilidad up -d` y esperar 30 s |
| Grafana o Prometheus no abren | se levantó sin el perfil de observabilidad | `docker compose --profile observabilidad up -d` |
| Muchos 404 o el control RLS falla con "No hay intentos" | se generaron datos durante otra prueba | repetir la corrida con el script y sin comandos en paralelo |
| p95 de autorización > 5 ms | poca CPU para Docker o la máquina ocupada | Docker Desktop → *Settings → Resources*: 4 o más CPU; cerrar programas; mover el proyecto fuera de OneDrive (por ejemplo a `C:\exp1`). Si aún no alcanza: archivo `.env` con `GATEWAY_WORKERS=3` y `docker compose up -d --force-recreate gateway opa-sidecar` |
| `Ataques registrados` < 100 % | el broker o el consumidor de logs no estaban listos | `docker compose ps` (el `broker` debe estar `healthy`), `docker compose logs logs-seguridad`; volver a reconciliar al minuto |
| 503 `pdp_no_disponible` | se recreó el gateway sin el sidecar | `docker compose up -d --force-recreate gateway opa-sidecar` |
| 401 en todo | se regeneraron las llaves con el sistema arriba | `docker compose up -d --force-recreate` |
| `cannot be loaded because running scripts is disabled` | política de ejecución de PowerShell | ejecuta el script como se indica aquí, con `powershell -ExecutionPolicy Bypass -File ...` |
| `Conflict. The container name "/exp1-k6-..." is already in use` | quedó un k6 de una corrida interrumpida | `docker rm -f exp1-k6-corrida1` (con el nombre que corresponda) |

Si interrumpes una corrida con Ctrl+C, detén también el k6 que quedó en segundo plano:

```powershell
docker ps --filter "name=exp1-k6" -q | ForEach-Object { docker rm -f $_ }
```

## F. Al terminar la sesión

```powershell
docker compose --profile observabilidad stop     # apaga todo y conserva los datos para la próxima sesión
```

`docker compose down -v` solo si quieres borrar también los volúmenes (BD y Redis). No hace falta entre corridas, porque cada corrida limpia sus datos al empezar.
