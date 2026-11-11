"""Logs de seguridad (vista de seguridad): consume AccesoDenegado del broker y lo guarda
en la tabla log_seguridad, que es solo de inserción. Confirma el mensaje después de
insertarlo, así un reinicio no pierde eventos.
"""
import asyncio
import json
import logging
import os
from datetime import datetime

import aio_pika
import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("logs_seguridad")

AMQP_URL = os.getenv("AMQP_URL", "amqp://guest:guest@broker/")
DB_URL = os.getenv("DB_URL", "postgresql://app_logs:app_logs@postgres/evaluaciones")
COLA = "seguridad.acceso_denegado"

INSERTAR = (
    "INSERT INTO log_seguridad (fecha, servicio, usuario, tenant_id, ip, recurso, metodo, motivo) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)"
)


async def conectar_con_reintentos(funcion, nombre):
    while True:
        try:
            return await funcion()
        except Exception as e:
            log.warning("%s no disponible (%s); reintento en 2 s", nombre, e)
            await asyncio.sleep(2)


async def principal() -> None:
    pool = await conectar_con_reintentos(lambda: asyncpg.create_pool(DB_URL, min_size=1, max_size=4), "PostgreSQL")
    conexion = await conectar_con_reintentos(lambda: aio_pika.connect_robust(AMQP_URL), "RabbitMQ")
    canal = await conexion.channel()
    await canal.set_qos(prefetch_count=100)
    cola = await canal.declare_queue(COLA, durable=True)
    log.info("Escuchando %s", COLA)

    async with cola.iterator() as mensajes:
        async for mensaje in mensajes:
            async with mensaje.process(requeue=True):   # ack solo si el INSERT funcionó
                e = json.loads(mensaje.body)
                await pool.execute(
                    INSERTAR,
                    datetime.fromisoformat(e["fecha"]), e.get("servicio"), e.get("usuario"),
                    e.get("tenant_id"), e.get("ip"), e.get("recurso"), e.get("metodo"), e.get("motivo"),
                )


if __name__ == "__main__":
    asyncio.run(principal())
