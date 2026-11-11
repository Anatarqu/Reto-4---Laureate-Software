"""Publicación del evento AccesoDenegado hacia RabbitMQ (vista de seguridad:
Respuesta de denegación -> Broker de eventos -> Logs de seguridad).

La publicación es asíncrona para no sumar latencia a la respuesta del usuario.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone

import aio_pika
from prometheus_client import Counter

COLA_DENEGADOS = "seguridad.acceso_denegado"

DENEGADOS = Counter(
    "accesos_denegados_total",
    "Accesos denegados detectados por el servicio",
    ["servicio", "motivo"],
)
NO_PUBLICADOS = Counter(
    "accesos_denegados_no_publicados_total",
    "Eventos AccesoDenegado que no se pudieron publicar en el broker",
    ["servicio"],
)

log = logging.getLogger("eventos")


class PublicadorDenegados:
    def __init__(self, amqp_url: str, servicio: str):
        self._url = amqp_url
        self._servicio = servicio
        self._conexion = None
        self._canal = None
        self._tareas: set[asyncio.Task] = set()
        self._tarea_conexion: asyncio.Task | None = None

    async def iniciar(self) -> None:
        """Conecta en segundo plano y reintenta hasta que el broker esté listo."""
        self._tarea_conexion = asyncio.create_task(self._conectar())

    async def _conectar(self) -> None:
        while self._canal is None:
            try:
                self._conexion = await aio_pika.connect_robust(self._url)
                canal = await self._conexion.channel()
                await canal.declare_queue(COLA_DENEGADOS, durable=True)
                self._canal = canal
                log.info("Conectado a RabbitMQ")
            except Exception as e:  # el broker aún no está listo
                log.warning("RabbitMQ no disponible (%s); reintento en 2 s", e)
                await asyncio.sleep(2)

    def registrar(self, usuario, tenant_id, ip, recurso, metodo, motivo) -> None:
        DENEGADOS.labels(self._servicio, motivo.split(":")[0]).inc()
        evento = {
            "fecha": datetime.now(timezone.utc).isoformat(),
            "servicio": self._servicio,
            "usuario": usuario,
            "tenant_id": tenant_id,
            "ip": ip,
            "recurso": recurso,
            "metodo": metodo,
            "motivo": motivo,
        }
        if self._canal is None:
            NO_PUBLICADOS.labels(self._servicio).inc()
            log.error("AccesoDenegado sin publicar (broker no conectado): %s", evento)
            return
        tarea = asyncio.create_task(self._publicar(evento))
        self._tareas.add(tarea)
        tarea.add_done_callback(self._tareas.discard)

    async def _publicar(self, evento: dict) -> None:
        try:
            await self._canal.default_exchange.publish(
                aio_pika.Message(
                    json.dumps(evento).encode(),
                    content_type="application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                ),
                routing_key=COLA_DENEGADOS,
            )
        except Exception as e:
            NO_PUBLICADOS.labels(self._servicio).inc()
            log.error("No se pudo publicar AccesoDenegado: %s", e)

    async def cerrar(self) -> None:
        if self._tarea_conexion is not None and not self._tarea_conexion.done():
            self._tarea_conexion.cancel()
        if self._tareas:
            await asyncio.gather(*self._tareas, return_exceptions=True)
        if self._conexion is not None:
            await self._conexion.close()
