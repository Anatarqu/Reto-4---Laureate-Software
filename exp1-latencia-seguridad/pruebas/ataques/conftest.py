import json
import os

import httpx
import pytest

AQUI = os.path.dirname(os.path.abspath(__file__))
GATEWAY_URL = os.getenv("GATEWAY_URL", "https://balanceador:8443")
RESPUESTAS_DIRECTO_URL = os.getenv("RESPUESTAS_DIRECTO_URL", "http://respuestas:8081")
DB_APP_URL = os.getenv("DB_APP_URL", "postgresql://app_eval:app_eval@postgres/evaluaciones")


def cargar_datos() -> dict:
    with open(os.path.join(AQUI, "datos_ataques.json"), encoding="utf-8") as f:
        return json.load(f)


DATOS = cargar_datos()


@pytest.fixture(scope="session")
def gateway():
    with httpx.Client(base_url=GATEWAY_URL, verify=False, timeout=10.0) as c:
        yield c


@pytest.fixture(scope="session")
def directo():
    with httpx.Client(base_url=RESPUESTAS_DIRECTO_URL, timeout=10.0) as c:
        yield c
