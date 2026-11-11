"""Persistidor (vista de latencia: Lee del log -> Upsert por seq -> Confirma).

Drena el stream log:respuestas de Redis con un grupo de consumidores y escribe en la
BD de Intentos. Respeta Row-Level Security fijando la institución de cada mensaje.
Solo confirma (XACK) después del commit, así un reinicio no pierde respuestas.
"""
import asyncio
import logging
import os
import socket
import time
import uuid

import asyncpg
import redis.asyncio as redis
from prometheus_client import Counter, Gauge, start_http_server

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("persistidor")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis-primario:6379")
DB_URL = os.getenv("DB_URL", "postgresql://app_eval:app_eval@postgres/evaluaciones")
PUERTO_METRICAS = int(os.getenv("PUERTO_METRICAS", "8002"))
STREAM, GRUPO = "log:respuestas", "persistidores"
CONSUMIDOR = os.getenv("HOSTNAME", socket.gethostname())

ESCRITAS = Counter("persistidor_respuestas_escritas_total", "Respuestas escritas en PostgreSQL")
PENDIENTES = Gauge("persistidor_pendientes", "Mensajes entregados y aún sin confirmar")
REZAGO = Gauge("persistidor_rezago", "Mensajes del stream aún no leídos por el grupo")

UPSERT = (
    "INSERT INTO respuestas (tenant_id, intento_id, pregunta_id, valor, seq, answer_id) "
    "VALUES ($1, $2, $3, $4, $5, $6) "
    "ON CONFLICT (intento_id, pregunta_id) DO UPDATE "
    "SET valor = EXCLUDED.valor, seq = EXCLUDED.seq, answer_id = EXCLUDED.answer_id, actualizado = now() "
    "WHERE respuestas.seq < EXCLUDED.seq"
)


async def asegurar_grupo(r) -> None:
    try:
        await r.xgroup_create(STREAM, GRUPO, id="0", mkstream=True)
        log.info("Grupo %s creado", GRUPO)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


async def escribir(pool, r, mensajes) -> None:
    async with pool.acquire() as con:
        async with con.transaction():
            for _id, m in mensajes:
                await con.execute("SELECT set_config('app.tenant_id', $1, true)", m["tenant"])
                await con.execute(
                    UPSERT, m["tenant"], uuid.UUID(m["intento"]), m["pregunta"], m["valor"],
                    int(m["seq"]), m["answer_id"],
                )
    await r.xack(STREAM, GRUPO, *[i for i, _ in mensajes])
    ESCRITAS.inc(len(mensajes))


async def actualizar_metricas(r) -> None:
    try:
        for g in await r.xinfo_groups(STREAM):
            if g["name"] == GRUPO:
                PENDIENTES.set(g.get("pending", 0) or 0)
                REZAGO.set(g.get("lag", 0) or 0)
    except redis.ResponseError:
        pass


async def principal() -> None:
    start_http_server(PUERTO_METRICAS)
    r = redis.from_url(REDIS_URL, decode_responses=True)
    while True:
        try:
            pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=4)
            break
        except Exception as e:
            log.warning("PostgreSQL no disponible (%s); reintento en 2 s", e)
            await asyncio.sleep(2)
    await asegurar_grupo(r)
    log.info("Persistidor %s drenando %s", CONSUMIDOR, STREAM)

    desde = "0"           # primero, lo que quedó pendiente de este consumidor
    ultima_metrica = 0.0
    while True:
        try:
            lotes = await r.xreadgroup(GRUPO, CONSUMIDOR, {STREAM: desde}, count=200, block=1000)
        except redis.ResponseError as e:
            # NOGROUP / UNBLOCKED: el stream se borró (limpieza entre corridas con generar_datos.py).
            if "NOGROUP" in str(e) or "UNBLOCKED" in str(e):
                log.info("El stream se reinició; se vuelve a crear el grupo")
                await asegurar_grupo(r)
                desde = "0"
                continue
            raise
        except (redis.ConnectionError, redis.TimeoutError) as e:
            log.warning("Redis no disponible (%s); reintento en 2 s", e)
            await asyncio.sleep(2)
            continue
        hubo = False
        for _, mensajes in lotes or []:
            if mensajes:
                hubo = True
                try:
                    await escribir(pool, r, mensajes)
                except (asyncpg.PostgresError, OSError) as e:
                    # Sin XACK: los mensajes quedan pendientes y se reintentan al leer desde "0".
                    log.warning("No se pudo escribir el lote (%s); se reintenta", e)
                    desde = "0"
                    await asyncio.sleep(1)
        if desde == "0" and not hubo:
            desde = ">"                    # pendientes atendidos; ahora solo mensajes nuevos
        if time.monotonic() - ultima_metrica > 2:
            await actualizar_metricas(r)
            ultima_metrica = time.monotonic()


if __name__ == "__main__":
    asyncio.run(principal())
