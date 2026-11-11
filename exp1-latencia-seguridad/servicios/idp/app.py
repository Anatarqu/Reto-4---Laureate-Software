"""Proveedor de Identidad simplificado SOLO PARA EL LABORATORIO.

Emite tokens RS256 con sub, tenant_id y rol. No valida contraseñas: en el experimento
los tokens de la carga se generan con herramientas/generar_datos.py usando la misma
llave privada. Este servicio existe para mostrar el componente de la vista y para
pedir tokens a mano.
"""
import os
import time

import jwt
from fastapi import FastAPI
from pydantic import BaseModel

LLAVE_PRIVADA = os.getenv("LLAVE_PRIVADA", "/claves/jwt_priv.pem")
LLAVE_PUBLICA = os.getenv("LLAVE_PUBLICA", "/claves/jwt_pub.pem")

with open(LLAVE_PRIVADA, encoding="utf-8") as f:
    PRIVADA = f.read()
with open(LLAVE_PUBLICA, encoding="utf-8") as f:
    PUBLICA = f.read()

app = FastAPI(title="Proveedor de Identidad (laboratorio)")


class SolicitudToken(BaseModel):
    sub: str
    tenant_id: str
    rol: str = "estudiante"
    minutos: int = 120


@app.get("/salud")
async def salud():
    return {"ok": True}


@app.post("/token")
async def emitir(s: SolicitudToken):
    ahora = int(time.time())
    token = jwt.encode(
        {"sub": s.sub, "tenant_id": s.tenant_id, "rol": s.rol, "iat": ahora, "exp": ahora + s.minutos * 60},
        PRIVADA,
        algorithm="RS256",
    )
    return {"access_token": token, "token_type": "Bearer"}


@app.get("/llave-publica")
async def llave_publica():
    return {"pem": PUBLICA}
