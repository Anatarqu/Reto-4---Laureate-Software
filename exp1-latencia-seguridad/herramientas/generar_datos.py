"""Deja el laboratorio en estado inicial y genera los datos de prueba de una corrida.

Qué hace (en cada ejecución):
  1. Vacía intentos, respuestas y log_seguridad, y limpia Redis (FLUSHALL).
  2. Crea 2 instituciones (inst-a, inst-b) con N estudiantes cada una y un intento
     abierto por estudiante, en PostgreSQL y en Redis (hash intento:{id}).
  3. Firma un token RS256 por estudiante y 2 docentes por institución.
  4. Escribe:
       pruebas/k6/estudiantes.json          (carga legítima de k6)
       pruebas/ataques/datos_ataques.json   (insumos de la suite de ataques)

Uso:  docker compose run --rm herramientas python herramientas/generar_datos.py
      (opcional: --por-institucion 500)
"""
import argparse
import asyncio
import json
import os
import time
import uuid

import asyncpg
import jwt
import redis.asyncio as redis

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_ADMIN_URL = os.getenv("DB_ADMIN_URL", "postgresql://postgres:postgres@postgres/evaluaciones")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis-primario:6379")
INSTITUCIONES = ["inst-a", "inst-b"]


def leer(ruta: str) -> str:
    with open(os.path.join(BASE, ruta), encoding="utf-8") as f:
        return f.read()


def firmar(llave: str, sub: str, tenant: str, rol: str, minutos: int = 240) -> str:
    ahora = int(time.time())
    return jwt.encode(
        {"sub": sub, "tenant_id": tenant, "rol": rol, "iat": ahora, "exp": ahora + minutos * 60},
        llave,
        algorithm="RS256",
    )


def alterar(token: str, nuevo_tenant: str) -> str:
    """Cambia el tenant_id del payload sin volver a firmar (la firma deja de coincidir)."""
    import base64

    cabecera, payload, firma = token.split(".")
    relleno = "=" * (-len(payload) % 4)
    datos = json.loads(base64.urlsafe_b64decode(payload + relleno))
    datos["tenant_id"] = nuevo_tenant
    nuevo = base64.urlsafe_b64encode(json.dumps(datos, separators=(",", ":")).encode()).rstrip(b"=").decode()
    return f"{cabecera}.{nuevo}.{firma}"


async def main(por_institucion: int) -> None:
    privada = leer("claves/jwt_priv.pem")

    # Primero se prepara todo en memoria (firmar 1.000 tokens tarda unos segundos) y solo
    # al final se limpia y se carga, en un único paso. Así la BD nunca queda vacía a medias.
    print("Firmando tokens y preparando intentos...")
    estudiantes = []
    filas = []
    for tenant in INSTITUCIONES:
        letra = tenant[-1]
        for n in range(1, por_institucion + 1):
            est_id = f"est-{letra}-{n:04d}"
            intento = str(uuid.uuid4())
            filas.append((uuid.UUID(intento), tenant, est_id, "abierto"))
            estudiantes.append({
                "tenant": tenant,
                "id": est_id,
                "intento": intento,
                "token": firmar(privada, est_id, tenant, "estudiante"),
            })

    con = await asyncpg.connect(DB_ADMIN_URL)
    r = redis.from_url(REDIS_URL, decode_responses=True)

    print(f"Limpiando y cargando {len(filas)} intentos en PostgreSQL (una transacción)...")
    async with con.transaction():
        await con.execute("TRUNCATE intentos, respuestas, log_seguridad RESTART IDENTITY")
        await con.copy_records_to_table("intentos", records=filas, columns=["id", "tenant_id", "estudiante_id", "estado"])

    print("Limpiando y cargando los intentos en Redis...")
    async with r.pipeline(transaction=True) as p:
        p.flushall()
        for e in estudiantes:
            p.hset(f"intento:{e['intento']}", mapping={
                "owner": e["id"], "tenant": e["tenant"], "estado": "abierto", "seq": 0,
            })
        await p.execute()

    # Intercalar instituciones para que la carga de k6 mezcle ambas.
    a = [e for e in estudiantes if e["tenant"] == "inst-a"]
    b = [e for e in estudiantes if e["tenant"] == "inst-b"]
    mezclados = [x for par in zip(a, b) for x in par]
    with open(os.path.join(BASE, "pruebas/k6/estudiantes.json"), "w", encoding="utf-8") as f:
        json.dump(mezclados, f)

    # Insumos de la suite de ataques: 20 parejas (A_i de inst-a, B_i de inst-b) y docentes.
    parejas = []
    for i in range(20):
        ea, eb = a[i], b[i]
        parejas.append({
            "a": ea,
            "b": eb,
            "token_alterado": alterar(ea["token"], "inst-b"),
            "token_vencido": firmar(privada, ea["id"], ea["tenant"], "estudiante", minutos=-10),
        })
    docentes = {
        t: [{"id": f"doc-{t[-1]}-{k}", "token": firmar(privada, f"doc-{t[-1]}-{k}", t, "docente")} for k in (1, 2)]
        for t in INSTITUCIONES
    }
    with open(os.path.join(BASE, "pruebas/ataques/datos_ataques.json"), "w", encoding="utf-8") as f:
        json.dump({"parejas": parejas, "docentes": docentes}, f)

    await con.close()
    await r.aclose()
    print(f"Listo: {len(estudiantes)} estudiantes, 20 parejas para 120 ataques, 4 docentes.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--por-institucion", type=int, default=500)
    args = parser.parse_args()
    asyncio.run(main(args.por_institucion))
