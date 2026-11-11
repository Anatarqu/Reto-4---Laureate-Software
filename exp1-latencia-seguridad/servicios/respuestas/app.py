"""Servicio de Intentos y respuestas (vista de seguridad) / Respuestas ×N (vista de latencia):
Validar petición -> Script atómico -> Esperar réplica -> Responder 200.

Confirma tras guardar en Redis (hash del intento + stream log:respuestas) y esperar a que la
réplica de Redis tenga la copia. El Persistidor escribe después en la BD Intentos y Respuestas.

El servicio vuelve a validar el JWT y que el intento sea del estudiante, para que
llamarlo directamente (sin pasar por el gateway) no sirva (defensa en profundidad).
"""
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager

import asyncpg
import redis.asyncio as redis
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Histogram, generate_latest

from comun.eventos import PublicadorDenegados
from comun.seguridad import TokenInvalido, ValidadorJWT, ip_cliente

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("respuestas")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis-primario:6379")
DB_URL = os.getenv("DB_URL", "postgresql://app_eval:app_eval@postgres/evaluaciones")
AMQP_URL = os.getenv("AMQP_URL", "amqp://guest:guest@broker/")
LLAVE_PUBLICA = os.getenv("LLAVE_PUBLICA", "/claves/jwt_pub.pem")
REPLICAS_MIN = int(os.getenv("REDIS_REPLICAS_MIN", "1"))      # 0 desactiva la espera
ESPERA_REPLICA_MS = int(os.getenv("REDIS_ESPERA_REPLICA_MS", "100"))

STREAM = "log:respuestas"
PATRON_PREGUNTA = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

# Script atómico: valida dueño y estado, guarda la respuesta y la agrega al stream.
SCRIPT_GUARDAR = """
local duenio = redis.call('HGET', KEYS[1], 'owner')
if not duenio then return redis.error_reply('NO_INTENTO') end
if duenio ~= ARGV[1] then return redis.error_reply('NO_DUENO') end
if redis.call('HGET', KEYS[1], 'estado') ~= 'abierto' then return redis.error_reply('CERRADO') end
local seq = redis.call('HINCRBY', KEYS[1], 'seq', 1)
redis.call('HSET', KEYS[1], 'p:' .. ARGV[2], ARGV[3])
redis.call('XADD', KEYS[2], '*', 'intento', string.sub(KEYS[1], 9), 'pregunta', ARGV[2],
  'valor', ARGV[3], 'answer_id', ARGV[4], 'tenant', ARGV[5], 'seq', seq)
return seq
"""

GUARDADO = Histogram(
    "guardado_seconds",
    "Tiempo de confirmación del guardado dentro del servicio de Respuestas",
    ["resultado"],
    buckets=(0.002, 0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.4, 0.6, 1.0, 2.0),
)
ESPERA_REPLICA = Histogram(
    "espera_replica_seconds",
    "Tiempo del WAIT a la réplica de Redis",
    buckets=(0.0005, 0.001, 0.002, 0.005, 0.01, 0.025, 0.05, 0.1, 0.2),
)

validador = ValidadorJWT(LLAVE_PUBLICA)
publicador = PublicadorDenegados(AMQP_URL, "respuestas")


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    app.state.redis = redis.from_url(REDIS_URL, decode_responses=True)
    app.state.guardar = app.state.redis.register_script(SCRIPT_GUARDAR)
    app.state.pool = await asyncpg.create_pool(DB_URL, min_size=2, max_size=20)
    await publicador.iniciar()
    log.info("Respuestas listo. REPLICAS_MIN=%s", REPLICAS_MIN)
    yield
    await app.state.pool.close()
    await app.state.redis.aclose()
    await publicador.cerrar()


app = FastAPI(title="Respuestas", lifespan=ciclo_de_vida)


@app.get("/metrics")
async def metricas():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/salud")
async def salud(request: Request):
    try:
        await request.app.state.redis.ping()
    except Exception:
        return Response(status_code=503)
    return {"ok": True}


def denegar(request: Request, claims: dict | None, motivo: str, estado: int) -> Response:
    claims = claims or {}
    publicador.registrar(
        usuario=claims.get("sub"),
        tenant_id=claims.get("tenant_id"),
        ip=ip_cliente(request),
        recurso=request.url.path,
        metodo=request.method,
        motivo=motivo,
    )
    return Response(status_code=estado)


@app.put("/api/instituciones/{tenant}/estudiantes/{estudiante}/intentos/{intento}/respuestas/{pregunta}")
async def guardar_respuesta(tenant: str, estudiante: str, intento: str, pregunta: str, request: Request):
    t0 = time.perf_counter()

    # Validar petición: token, ruta coherente con el token y datos de entrada.
    try:
        claims = validador.validar(request.headers.get("authorization"))
    except TokenInvalido as e:
        return denegar(request, None, e.motivo, 401)
    if claims.get("rol") != "estudiante" or claims["tenant_id"] != tenant or claims["sub"] != estudiante:
        return denegar(request, claims, "ruta_no_coincide_con_token", 403)
    try:
        intento_uuid = uuid.UUID(intento)
        cuerpo = await request.json()
        valor = str(cuerpo["valor"])[:2000]
        answer_id = str(cuerpo["answer_id"])[:80]
    except Exception:
        return JSONResponse({"error": "entrada_invalida"}, status_code=400)
    if not PATRON_PREGUNTA.match(pregunta):
        return JSONResponse({"error": "pregunta_invalida"}, status_code=400)

    try:
        seq = await request.app.state.guardar(
            keys=[f"intento:{intento_uuid}", STREAM],
            args=[claims["sub"], pregunta, valor, answer_id, claims["tenant_id"]],
        )
    except redis.ResponseError as e:
        mensaje = str(e)
        if "NO_DUENO" in mensaje:
            return denegar(request, claims, "intento_ajeno", 403)
        if "NO_INTENTO" in mensaje:
            return JSONResponse({"error": "intento_no_existe"}, status_code=404)
        if "CERRADO" in mensaje:
            return JSONResponse({"error": "intento_cerrado"}, status_code=409)
        raise
    if REPLICAS_MIN > 0:
        t_wait = time.perf_counter()
        confirmadas = await request.app.state.redis.wait(REPLICAS_MIN, ESPERA_REPLICA_MS)
        ESPERA_REPLICA.observe(time.perf_counter() - t_wait)
        if confirmadas < REPLICAS_MIN:
            GUARDADO.labels("sin_replica").observe(time.perf_counter() - t0)
            return JSONResponse({"error": "sin_copia_en_replica"}, status_code=503)

    GUARDADO.labels("ok").observe(time.perf_counter() - t0)
    return {"guardado": True, "seq": seq}


@app.get("/api/instituciones/{tenant}/cursos/{curso}/calificaciones")
async def calificaciones(tenant: str, curso: str, request: Request):
    """Endpoint de docente: sirve para probar el ataque 'estudiante usando un endpoint de docente'."""
    try:
        claims = validador.validar(request.headers.get("authorization"))
    except TokenInvalido as e:
        return denegar(request, None, e.motivo, 401)
    if claims.get("rol") != "docente" or claims["tenant_id"] != tenant:
        return denegar(request, claims, "rol_no_autorizado", 403)
    async with request.app.state.pool.acquire() as con:
        async with con.transaction():
            await con.execute("SELECT set_config('app.tenant_id', $1, true)", claims["tenant_id"])
            total = await con.fetchval("SELECT count(*) FROM respuestas")
    return {"curso": curso, "respuestas_registradas": total}
