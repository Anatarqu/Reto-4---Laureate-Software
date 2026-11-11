"""Reconciliación de una corrida (evidencia para REND-01 y SEG-01).

1. Espera a que el Persistidor termine de drenar el stream (rezago y pendientes en 0).
2. Compara los guardados que k6 recibió como confirmados con las filas en la BD.
3. Si se pasa el reporte de pytest, compara los ataques bloqueados con log_seguridad.

Uso:
  docker compose run --rm herramientas python herramientas/reconciliar.py \
      --k6 resultados/corrida1.json --ataques resultados/ataques1.xml --salida resultados/corrida1_resumen.txt
"""
import argparse
import asyncio
import builtins
import json
import os
import time
import xml.etree.ElementTree as ET

import asyncpg
import redis.asyncio as redis

DB_ADMIN_URL = os.getenv("DB_ADMIN_URL", "postgresql://postgres:postgres@postgres/evaluaciones")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis-primario:6379")
STREAM, GRUPO = "log:respuestas", "persistidores"


def confirmados_k6(ruta: str) -> int:
    with open(ruta, encoding="utf-8") as f:
        metricas = json.load(f)["metrics"]
    m = metricas.get("guardados_confirmados", {})
    return int(m.get("count", m.get("values", {}).get("count", 0)))


def contador_k6(ruta: str, nombre: str) -> int:
    with open(ruta, encoding="utf-8") as f:
        m = json.load(f)["metrics"].get(nombre, {})
    return int(m.get("count", m.get("values", {}).get("count", 0)))


def latencias_k6(ruta: str) -> dict:
    """p95/p99 del guardado y p95 de autorización desde el resumen de k6."""
    with open(ruta, encoding="utf-8") as f:
        m = json.load(f)["metrics"]
    guardado = m.get("http_req_duration{tipo:guardado}", m.get("http_req_duration", {}))
    guardado = guardado.get("values", guardado)
    authz = m.get("autorizacion_ms", {})
    authz = authz.get("values", authz)
    return {"p95": guardado.get("p(95)"), "p99": guardado.get("p(99)"), "authz_p95": authz.get("p(95)")}


def ataques_pytest(ruta: str) -> tuple[int, int, int, int]:
    """Devuelve (ataques, ataques bloqueados, controles, controles correctos)."""
    ataques = bloqueados = controles = controles_ok = 0
    for caso in ET.parse(ruta).getroot().iter("testcase"):
        fallo = caso.find("failure") is not None or caso.find("error") is not None
        if caso.get("name", "").startswith("test_ataque"):
            ataques += 1
            bloqueados += 0 if fallo else 1
        elif caso.get("name", "").startswith("test_control"):
            controles += 1
            controles_ok += 0 if fallo else 1
    return ataques, bloqueados, controles, controles_ok


async def esperar_drenado(r, limite_s: int = 600) -> None:
    inicio = time.time()
    while time.time() - inicio < limite_s:
        try:
            grupos = await r.xinfo_groups(STREAM)
        except redis.ResponseError:
            return  # no hay stream (configuración A)
        g = next((g for g in grupos if g["name"] == GRUPO), None)
        if g is None:
            return
        pendientes, rezago = g.get("pending", 0) or 0, g.get("lag", 0) or 0
        if pendientes == 0 and rezago == 0:
            return
        print(f"  esperando al Persistidor: rezago={rezago} pendientes={pendientes}")
        await asyncio.sleep(2)
    print("  ADVERTENCIA: el stream no terminó de drenarse en el tiempo límite")


async def main(args) -> None:
    r = redis.from_url(REDIS_URL, decode_responses=True)
    print("Esperando a que el Persistidor drene el stream...")
    await esperar_drenado(r)
    await r.aclose()

    con = await asyncpg.connect(DB_ADMIN_URL)
    filas = await con.fetchval("SELECT count(*) FROM respuestas WHERE answer_id NOT LIKE 'ataque-%'")
    denegados = await con.fetchval("SELECT count(*) FROM log_seguridad")
    por_motivo = await con.fetch("SELECT motivo, count(*) AS n FROM log_seguridad GROUP BY motivo ORDER BY n DESC")
    await con.close()

    print("\n=== REND-01 · respuestas perdidas ===")
    if args.k6:
        confirmados = confirmados_k6(args.k6)
        perdidas = confirmados - filas
        print(f"  confirmadas por k6 : {confirmados}")
        print(f"  filas en la BD     : {filas}")
        print(f"  perdidas           : {max(perdidas, 0)}   (meta: 0)")
        print(f"  errores HTTP vistos por k6 : {contador_k6(args.k6, 'errores_guardado')}"
              f"   reintentos del portal: {contador_k6(args.k6, 'reintentos_guardado')}")
        if filas > confirmados:
            print("  (hay más filas que confirmados: guardados que el servidor sí hizo pero k6 no alcanzó a ver)")
    else:
        print(f"  filas en la BD: {filas} (pasa --k6 para comparar)")

    print("\n=== SEG-01 · ataques bloqueados y registrados ===")
    if args.ataques:
        ataques, bloqueados, controles, controles_ok = ataques_pytest(args.ataques)
        print(f"  ataques intentados          : {ataques}")
        print(f"  ataques bloqueados          : {bloqueados}   ({100 * bloqueados / max(ataques, 1):.1f} %, meta: 100 %)")
        print(f"  ataques registrados en logs : {denegados}   ({100 * denegados / max(ataques, 1):.1f} %, meta: 100 %)")
        print(f"  controles correctos         : {controles_ok} de {controles}")
    else:
        print(f"  filas en log_seguridad: {denegados} (pasa --ataques para comparar)")
    for fila in por_motivo:
        print(f"    {fila['motivo']:<32} {fila['n']}")

    if args.k6 and args.ataques:
        lat = latencias_k6(args.k6)
        filas_resumen = [
            ("REND-01", "p95 del guardado", f"{lat['p95']:.0f} ms", lat["p95"] <= 400, "<= 400 ms"),
            ("REND-01", "p99 del guardado", f"{lat['p99']:.0f} ms", lat["p99"] <= 600, "<= 600 ms"),
            ("REND-01", "Respuestas perdidas", str(max(perdidas, 0)), perdidas <= 0, "0"),
            ("SEG-01", "Ataques bloqueados", f"{100 * bloqueados / max(ataques, 1):.1f} %", bloqueados == ataques, "100 %"),
            ("SEG-01", "p95 de autorización", f"{lat['authz_p95']:.2f} ms", lat["authz_p95"] <= 5, "<= 5 ms"),
            ("SEG-01", "Ataques registrados", f"{100 * denegados / max(ataques, 1):.1f} %", denegados >= ataques, "100 %"),
        ]
        print("\n=== Resumen de la corrida (para la tabla de resultados) ===")
        for asr, metrica, valor, cumple, meta in filas_resumen:
            print(f"  {asr:<8} {metrica:<24} {valor:>10}   meta {meta:<10} {'CUMPLE' if cumple else 'NO CUMPLE'}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--k6", help="resumen exportado por k6 (--summary-export)")
    p.add_argument("--ataques", help="reporte junit de pytest (--junitxml)")
    p.add_argument("--salida", help="además de mostrarlo, guarda el resumen en este archivo")
    args = p.parse_args()
    lineas: list[str] = []
    imprimir = builtins.print

    def print(*a, **k):  # noqa: A001 - copia cada línea para el archivo de salida
        lineas.append(" ".join(str(x) for x in a))
        imprimir(*a, **k)

    builtins.print = print
    asyncio.run(main(args))
    builtins.print = imprimir
    if args.salida:
        with open(args.salida, "w", encoding="utf-8") as f:
            f.write("\n".join(lineas) + "\n")
        imprimir(f"\nResumen guardado en {args.salida}")
