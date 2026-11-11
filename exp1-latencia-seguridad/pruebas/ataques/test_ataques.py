"""Suite de ataques del experimento 1 (SEG-01): se INTENTA el ataque y se verifica que se
bloquea. Cada ataque debe terminar en 401 o 403 y nunca en 200.

  6 tipos de ataque x 20 casos = 120 ataques
  + 2 controles (no son ataques): un docente legítimo sí entra, y la BD aísla instituciones.

Componente que debe detener cada ataque (vista de seguridad):
  credencial robada ............ Servicio de Decisiones (OPA) -> Aplicar decisión
  token alterado / vencido / sin token ... Validador JWT
  estudiante en endpoint docente ......... Servicio de Decisiones (OPA)
  llamada directa sin gateway ............ Servicio de Intentos y respuestas + Row-Level Security
"""
import asyncio

import asyncpg
import pytest

from conftest import DATOS, DB_APP_URL

PAREJAS = DATOS["parejas"]          # 20 parejas (estudiante A de inst-a, estudiante B de inst-b)
CASOS = range(len(PAREJAS))
BLOQUEADO = {401, 403}


def ruta_guardar(est: dict, pregunta: str = "q-ataque") -> str:
    return (
        f"/api/instituciones/{est['tenant']}/estudiantes/{est['id']}"
        f"/intentos/{est['intento']}/respuestas/{pregunta}"
    )


def cuerpo(i: int, tipo: str) -> dict:
    return {"valor": "X", "answer_id": f"ataque-{tipo}-{i}"}


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------- 120 ataques ----------------------------

@pytest.mark.parametrize("i", CASOS)
def test_ataque_credencial_robada(gateway, i):
    """Token válido del estudiante A usado contra el intento del estudiante B (otra institución)."""
    p = PAREJAS[i]
    r = gateway.put(ruta_guardar(p["b"]), json=cuerpo(i, "robada"), headers=bearer(p["a"]["token"]))
    assert r.status_code in BLOQUEADO, r.text


@pytest.mark.parametrize("i", CASOS)
def test_ataque_token_alterado(gateway, i):
    """Se cambia tenant_id en el payload sin volver a firmar."""
    p = PAREJAS[i]
    r = gateway.put(ruta_guardar(p["b"]), json=cuerpo(i, "alterado"), headers=bearer(p["token_alterado"]))
    assert r.status_code in BLOQUEADO, r.text


@pytest.mark.parametrize("i", CASOS)
def test_ataque_token_vencido(gateway, i):
    p = PAREJAS[i]
    r = gateway.put(ruta_guardar(p["a"]), json=cuerpo(i, "vencido"), headers=bearer(p["token_vencido"]))
    assert r.status_code in BLOQUEADO, r.text


@pytest.mark.parametrize("i", CASOS)
def test_ataque_sin_token(gateway, i):
    p = PAREJAS[i]
    r = gateway.put(ruta_guardar(p["a"]), json=cuerpo(i, "sin_token"))
    assert r.status_code in BLOQUEADO, r.text


@pytest.mark.parametrize("i", CASOS)
def test_ataque_estudiante_en_endpoint_de_docente(gateway, i):
    est = PAREJAS[i]["a"]
    r = gateway.get(f"/api/instituciones/{est['tenant']}/cursos/curso-1/calificaciones", headers=bearer(est["token"]))
    assert r.status_code in BLOQUEADO, r.text


@pytest.mark.parametrize("i", CASOS)
def test_ataque_llamada_directa_sin_gateway(directo, i):
    """Llamada directa al Servicio de Intentos y respuestas, con el token de A contra el intento de B."""
    p = PAREJAS[i]
    r = directo.put(ruta_guardar(p["b"]), json=cuerpo(i, "directo"), headers=bearer(p["a"]["token"]))
    assert r.status_code in BLOQUEADO, r.text


# ---------------------------- 2 controles ----------------------------

def test_control_docente_legitimo_si_entra(gateway):
    """Un docente de su propia institución sí puede consultar (no es un ataque)."""
    doc = DATOS["docentes"]["inst-a"][0]
    r = gateway.get("/api/instituciones/inst-a/cursos/curso-1/calificaciones", headers=bearer(doc["token"]))
    assert r.status_code == 200, r.text


def test_control_row_level_security_aisla_instituciones():
    """Con la sesión fijada en inst-a, la BD no devuelve filas de inst-b aunque se pidan."""

    async def consultar():
        con = await asyncpg.connect(DB_APP_URL)
        try:
            async with con.transaction():
                await con.execute("SELECT set_config('app.tenant_id', 'inst-a', true)")
                ajenas = await con.fetchval("SELECT count(*) FROM intentos WHERE tenant_id = 'inst-b'")
                propias = await con.fetchval("SELECT count(*) FROM intentos WHERE tenant_id = 'inst-a'")
            sin_sesion = await con.fetchval("SELECT count(*) FROM intentos")
            return ajenas, propias, sin_sesion
        finally:
            await con.close()

    ajenas, propias, sin_sesion = asyncio.run(consultar())
    assert ajenas == 0, "RLS dejó ver intentos de otra institución"
    assert propias > 0, "No hay intentos de inst-a: ejecuta generar_datos.py antes de la suite (y no durante)"
    assert sin_sesion == 0, "Sin institución en la sesión la BD no debería devolver filas"
