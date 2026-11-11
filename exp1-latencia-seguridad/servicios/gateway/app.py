"""Gateway Estudiantes (vista de latencia y vista de seguridad).

Componentes internos:
  - Validador JWT          -> autentica al actor (firma RS256 y vencimiento).
  - Extractor del sujeto   -> toma sub, tenant_id y rol del token.
  - Cliente PDP            -> pregunta al Servicio de Decisiones (OPA como sidecar, en localhost).
  - Aplicar decisión (PEP) -> si OPA niega, responde 403 y publica AccesoDenegado;
                              si permite, reenvía a Respuestas por el HAProxy interno.
"""
import logging
import os
import time
from contextlib import asynccontextmanager

import aiohttp
from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Histogram, generate_latest, multiprocess

from comun.eventos import PublicadorDenegados
from comun.seguridad import TokenInvalido, ValidadorJWT, ip_cliente

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("gateway")

PDP_URL = os.getenv("PDP_URL", "http://localhost:8181/v1/data/evaluaciones/authz/allow")
RESPUESTAS_URL = os.getenv("RESPUESTAS_URL", "http://balanceador:8090")
LLAVE_PUBLICA = os.getenv("LLAVE_PUBLICA", "/claves/jwt_pub.pem")
AMQP_URL = os.getenv("AMQP_URL", "amqp://guest:guest@broker/")

AUTHZ = Histogram(
    "authz_decision_seconds",
    "Tiempo de la decisión de autorización (llamada al PDP)",
    buckets=(0.0005, 0.001, 0.002, 0.003, 0.004, 0.005, 0.0075, 0.01, 0.02, 0.05, 0.1),
)
GATEWAY = Histogram(
    "gateway_request_seconds",
    "Tiempo total de la solicitud dentro del gateway",
    ["resultado"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.4, 0.6, 0.8, 1.0, 2.0),
)

validador = ValidadorJWT(LLAVE_PUBLICA)
publicador = PublicadorDenegados(AMQP_URL, "gateway")


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    # aiohttp con conexiones persistentes: bajo concurrencia agrega mucha menos latencia
    # que httpx a la llamada al PDP (medido: p95 de 5 ms frente a 50 ms con 20 llamadas en paralelo).
    app.state.pdp = aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=64, keepalive_timeout=60),
        timeout=aiohttp.ClientTimeout(total=1.0),
    )
    app.state.upstream = aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=256, keepalive_timeout=60),
        timeout=aiohttp.ClientTimeout(total=5.0),
    )
    await publicador.iniciar()
    log.info("Gateway listo. PDP_URL=%s", PDP_URL)
    yield
    await app.state.pdp.close()
    await app.state.upstream.close()
    await publicador.cerrar()


app = FastAPI(title="Gateway Estudiantes", lifespan=ciclo_de_vida)


@app.get("/metrics")
async def metricas():
    # Con varios workers de uvicorn las métricas se suman desde PROMETHEUS_MULTIPROC_DIR.
    if os.getenv("PROMETHEUS_MULTIPROC_DIR"):
        registro = CollectorRegistry()
        multiprocess.MultiProcessCollector(registro)
        return Response(generate_latest(registro), media_type=CONTENT_TYPE_LATEST)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/salud")
async def salud():
    return {"ok": True}


def denegar(request: Request, claims: dict | None, motivo: str, estado: int, t0: float,
            authz_ms: float | None = None) -> Response:
    claims = claims or {}
    publicador.registrar(
        usuario=claims.get("sub"),
        tenant_id=claims.get("tenant_id"),
        ip=ip_cliente(request),
        recurso=request.url.path,
        metodo=request.method,
        motivo=motivo,
    )
    GATEWAY.labels("denegado").observe(time.perf_counter() - t0)
    cabeceras = {"X-Authz-Ms": f"{authz_ms:.3f}"} if authz_ms is not None else None
    return Response(status_code=estado, headers=cabeceras)


@app.api_route("/api/{ruta:path}", methods=["GET", "PUT", "POST"])
async def pep(ruta: str, request: Request):
    t0 = time.perf_counter()

    # 1. Validador JWT + Extractor del sujeto
    try:
        claims = validador.validar(request.headers.get("authorization"))
    except TokenInvalido as e:
        return denegar(request, None, e.motivo, 401, t0)

    # 2. Cliente PDP -> Servicio de Decisiones (OPA sidecar en localhost)
    entrada = {
        "input": {
            "method": request.method,
            "path": ["api"] + [p for p in ruta.split("/") if p],
            "token": {k: claims.get(k) for k in ("sub", "tenant_id", "rol")},
        }
    }
    t_pdp = time.perf_counter()
    try:
        async with request.app.state.pdp.post(PDP_URL, json=entrada) as r:
            permitido = bool((await r.json()).get("result", False))
    except Exception as e:  # sin PDP no se deja pasar (falla cerrada)
        log.error("PDP no disponible: %s", e)
        return denegar(request, claims, "pdp_no_disponible", 503, t0)
    authz_s = time.perf_counter() - t_pdp
    AUTHZ.observe(authz_s)
    authz_ms = authz_s * 1000

    # 3. Aplicar decisión
    if not permitido:
        return denegar(request, claims, "politica_abac", 403, t0, authz_ms)

    # 4. Reenviar a Respuestas (HAProxy interno reparte entre réplicas)
    cabeceras = {
        "authorization": request.headers["authorization"],
        "content-type": request.headers.get("content-type", "application/json"),
        "x-forwarded-for": ip_cliente(request),
    }
    try:
        async with request.app.state.upstream.request(
            request.method, f"{RESPUESTAS_URL}/api/{ruta}", data=await request.body(), headers=cabeceras
        ) as r:
            contenido, estado = await r.read(), r.status
    except (aiohttp.ClientError, TimeoutError) as e:
        log.error("Respuestas no disponible: %s", e)
        GATEWAY.labels("error").observe(time.perf_counter() - t0)
        return Response(status_code=502, headers={"X-Authz-Ms": f"{authz_ms:.3f}"})

    GATEWAY.labels("ok" if estado < 400 else "rechazado").observe(time.perf_counter() - t0)
    # X-Authz-Ms: tiempo de la decisión de autorización; k6 lo registra como métrica (SEG-01 <= 5 ms).
    return Response(content=contenido, status_code=estado, media_type="application/json",
                    headers={"X-Authz-Ms": f"{authz_ms:.3f}"})
