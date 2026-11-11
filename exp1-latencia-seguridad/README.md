# Experimento 1 · Latencia del guardado y seguridad del acceso

Laboratorio local (Docker Desktop) para comprobar la hipótesis del E1:

- **REND-01**: guardar una respuesta con p95 ≤ 400 ms y p99 ≤ 600 ms, con 1.000 estudiantes y sin perder respuestas.
- **SEG-01**: 0 % de fuga entre estudiantes o instituciones, con la autorización en ≤ 5 ms. Cada ataque bloqueado debe quedar registrado en los Logs de seguridad.

## Componentes (iguales a las vistas)

| Vista | Contenedor |
|---|---|
| Portal (cliente) | `k6` (carga legítima) y `herramientas` (pytest con los ataques) |
| Balanceador | `balanceador` (HAProxy 2.8: TLS en 8443 y límite de tasa por token) |
| Gateway Estudiantes: Validador JWT, Extractor del sujeto, Cliente PDP, Aplicar decisión | `gateway` (FastAPI) |
| Servicio de Decisiones (sidecar) | `opa-sidecar` (OPA 0.70, comparte la red del gateway) |
| Repositorio de políticas | carpeta `opa/politicas/` |
| Pipeline de políticas | `pipeline-politicas` (`opa test`) |
| Proveedor de identidad | `idp` (firma JWT RS256, solo para el laboratorio) |
| Servicio de Intentos y respuestas ×2 | `respuestas` (2 réplicas; script Lua atómico + `WAIT` a la réplica) |
| Caché Redis primario y réplica | `redis-primario`, `redis-replica` |
| Persistidor | `persistidor` (stream → PostgreSQL, XACK después del commit) |
| BD Intentos y Respuestas con RLS | `postgres` (PostgreSQL 16) |
| Respuesta de denegación → Broker | publicador dentro de `gateway`/`respuestas` → `broker` (RabbitMQ) |
| Logs de seguridad | `logs-seguridad` (inserta en `log_seguridad`, tabla de solo inserción) |
| Observabilidad (opcional) | `prometheus`, `grafana` |

---

## 1. Requisitos

- **Docker Desktop** 4.30 o superior (incluye Compose v2). Windows 10/11 con WSL 2, macOS (Intel o Apple Silicon) o Linux con Docker Engine y el plugin compose.
- En Docker Desktop, en *Settings → Resources*, asigna **al menos 4 CPU y 8 GB de memoria**. Con menos, la prueba de 1.000 VU mide la máquina y no la arquitectura.
- Puertos libres: **8443** (balanceador), 15672 (consola de RabbitMQ) y, si usas observabilidad, 9090 y 3000.
- Unos 3 GB de disco para las imágenes.

Comprueba la instalación:

```bash
docker version
docker compose version
```

## 2. Descomprimir el proyecto

Descomprime `exp1-latencia-seguridad.zip` y abre una terminal **dentro de la carpeta** (PowerShell, Terminal de macOS o bash). Todos los comandos siguientes se ejecutan desde ahí.

```bash
cd exp1-latencia-seguridad
```

> **Windows:** usa PowerShell o la terminal de WSL. Los comandos son los mismos; cuando un comando ocupe varias líneas, escríbelo en una sola línea.

## 3. Construir la imagen de herramientas y generar llaves (una sola vez)

```bash
docker compose --profile herramientas build herramientas
docker compose run --rm herramientas python herramientas/preparar_llaves.py
```

Esto crea `certs/tls.pem` (certificado autofirmado para HAProxy) y `claves/jwt_priv.pem` y `claves/jwt_pub.pem` (las llaves del JWT). No las subas a ningún repositorio.

## 4. Pipeline de políticas (Repositorio → Pipeline → Servicio de Decisiones)

Antes de desplegar, valida la política Rego:

```bash
docker compose --profile pipeline run --rm pipeline-politicas
```

Deben pasar las **6 pruebas** (`PASS: 6/6`). Si alguna falla, no despliegues la política.

## 5. Levantar el sistema

```bash
docker compose --profile observabilidad up -d --build
docker compose --profile observabilidad ps
```

Espera de 30 a 60 s a que todo esté `running` (Postgres, Redis y RabbitMQ deben aparecer como `healthy`). Comprueba el acceso:

```bash
curl -k https://localhost:8443/salud
```

Respuesta esperada: `{"ok":true}`. En Windows PowerShell usa `curl.exe -k https://localhost:8443/salud`.

> Si cambias algo en `opa/politicas/`, vuelve a correr el paso 4 y luego `docker compose restart opa-sidecar`.

## 6. Corrida piloto (≈ 3 minutos)

Desde aquí todo lo hace **un solo script**, que ejecuta los pasos en el orden correcto: datos limpios, k6 en segundo plano, ataques durante la carga, espera a k6 y reconciliación. **No ejecutes otros comandos del experimento mientras corre.**

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -File .\correr_corrida.ps1 -Nombre piloto -VUS 100 -Duracion 1m
```

macOS / Linux:

```bash
VUS=100 DURACION=1m ./correr_corrida.sh piloto
```

Debes ver `122 passed` en pytest y, al final, la tabla de resumen con 6 filas. El resumen también queda en `resultados/piloto_resumen.txt`.

## 7. Corridas del experimento (3 corridas)

Cada corrida dura unos 12 minutos (rampa de 30 s, 10 min con 1.000 VU y reconciliación).

```powershell
powershell -ExecutionPolicy Bypass -File .\correr_corrida.ps1 -Nombre corrida1
powershell -ExecutionPolicy Bypass -File .\correr_corrida.ps1 -Nombre corrida2
powershell -ExecutionPolicy Bypass -File .\correr_corrida.ps1 -Nombre corrida3
```

(En macOS o Linux: `./correr_corrida.sh corrida1`, y así con las otras dos.)

Cada corrida deja 4 archivos en `resultados/`: `corridaN.json` (k6), `corridaN_ataques.xml` (pytest), `corridaN_k6.log` y `corridaN_resumen.txt` (las 6 métricas con CUMPLE o NO CUMPLE).

Para repetir corridas otro día con el sistema ya instalado, sigue **CORRIDAS.md**.

### Qué medir (las 6 métricas del resumen)

| ASR | Métrica | Meta | De dónde sale |
|---|---|---|---|
| REND-01 | p95 del guardado | ≤ 400 ms | k6 |
| REND-01 | p99 del guardado | ≤ 600 ms | k6 |
| REND-01 | Respuestas perdidas | 0 | confirmadas por k6 frente a filas en PostgreSQL |
| SEG-01 | Ataques bloqueados | 100 % | pytest (120 ataques) |
| SEG-01 | p95 de autorización | ≤ 5 ms | cabecera `X-Authz-Ms` medida por k6 |
| SEG-01 | Ataques registrados | 100 % | filas en `log_seguridad` frente a ataques |

La hipótesis se acepta si las 6 métricas **cumplen en las 3 corridas**. Los archivos de `resultados/` son la evidencia.

Los 120 ataques son 20 de cada tipo: token de otro estudiante, token alterado, token vencido, sin token, estudiante en un endpoint de docente y llamada directa al servicio sin pasar por el gateway. Los 2 controles son: un docente legítimo sí entra, y RLS aísla las instituciones.

## 8. Observabilidad (opcional)

```bash
docker compose --profile observabilidad up -d
```

- Grafana: <http://localhost:3000>, dashboard **Experimento 1** (entra sin usuario).
- Prometheus: <http://localhost:9090>.
- Consola de RabbitMQ: <http://localhost:15672> (guest / guest). Ahí ves la cola `seguridad.acceso_denegado`.

Para revisar los registros de seguridad directamente:

```bash
docker compose exec postgres psql -U postgres -d evaluaciones -c "SELECT motivo, count(*) FROM log_seguridad GROUP BY motivo;"
```

## 9. Apagar y limpiar

```bash
docker compose --profile observabilidad down        # detiene todo y conserva los datos
docker compose --profile observabilidad down -v     # además borra los volúmenes (BD y Redis)
```

---

## Solución de problemas

- **"port is already allocated"**: otro programa usa ese puerto. Ciérralo o cambia el puerto de la izquierda en `docker-compose.yml` (por ejemplo `"9443:8443"`) y usa ese puerto en `curl`.
- **El balanceador se reinicia en bucle**: falta `certs/tls.pem`. Ejecuta el paso 3 y luego `docker compose up -d`.
- **401 en todas las peticiones**: regeneraste las llaves después de levantar el sistema. Ejecuta `docker compose up -d --force-recreate` y luego `generar_datos.py`.
- **503 `pdp_no_disponible`**: el sidecar OPA no está corriendo. Si recreaste el gateway, también hay que recrear el sidecar: `docker compose up -d --force-recreate gateway opa-sidecar`.
- **503 `sin_copia_en_replica`**: la réplica de Redis no está sincronizada. Revisa `docker compose logs redis-replica`.
- **Muchos 404 en el piloto, o el control de RLS falla con "No hay intentos de inst-a"**: se ejecutó `generar_datos.py` mientras otra prueba corría (borra y recrea los intentos). Usa siempre `correr_corrida.ps1`, que respeta el orden.
- **El p95 de autorización pasa de 5 ms**: dale más CPU a Docker Desktop (al menos 4), cierra programas pesados y mueve el proyecto fuera de OneDrive (por ejemplo a `C:\exp1`). Si aún no alcanza, sube los workers del gateway: crea un archivo `.env` con `GATEWAY_WORKERS=3` y ejecuta `docker compose up -d --force-recreate gateway opa-sidecar`.
- **"Ataques registrados" por debajo de 100 %**: revisa que `broker` esté `healthy` y mira `docker compose logs logs-seguridad`. El reconciliador espera a que el Persistidor drene; si acabas de lanzar los ataques, dale unos segundos y vuelve a reconciliar.
- **Permiso denegado al escribir en `resultados/`** (Linux): `chmod 777 resultados`.
- **p95 alto ya en el piloto**: sube las CPU y la memoria de Docker Desktop y cierra otros programas pesados. k6 corre en la misma máquina y también consume CPU.
- **Mac con Apple Silicon**: todas las imágenes tienen versión arm64; si alguna falla, agrega `platform: linux/amd64` a ese servicio.

## Estructura

```
docker-compose.yml        orquestación de todos los componentes
haproxy/haproxy.cfg       balanceador (TLS, límite de tasa, backends)
opa/politicas/            Repositorio de políticas (authz.rego + pruebas)
db/init/                  esquema, RLS y log de seguridad de solo inserción
servicios/                gateway, respuestas, persistidor, logs_seguridad, idp, comun
herramientas/             llaves, datos de prueba y reconciliación
pruebas/k6/guardado.js    carga legítima (1.000 VU)
pruebas/ataques/          suite de 120 ataques + 2 controles (pytest)
correr_corrida.ps1 / .sh  una corrida completa en orden (datos, carga, ataques, reconciliación)
CORRIDAS.md               guía para repetir corridas con el sistema ya levantado
prometheus/, grafana/     observabilidad opcional
resultados/               aquí quedan los JSON y XML de cada corrida
```
