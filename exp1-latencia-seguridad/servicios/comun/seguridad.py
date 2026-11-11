"""Validación del JWT (táctica: autenticar actores / verificar integridad del mensaje)."""
import jwt

CLAIMS_OBLIGATORIOS = ["exp", "sub", "tenant_id", "rol"]


class TokenInvalido(Exception):
    """El token no existe, está vencido, fue alterado o no trae los datos mínimos."""

    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


class ValidadorJWT:
    def __init__(self, ruta_llave_publica: str):
        with open(ruta_llave_publica, encoding="utf-8") as f:
            self._llave = f.read()

    def validar(self, cabecera_authorization: str | None) -> dict:
        if not cabecera_authorization or not cabecera_authorization.startswith("Bearer "):
            raise TokenInvalido("sin_token")
        token = cabecera_authorization.removeprefix("Bearer ").strip()
        try:
            return jwt.decode(
                token,
                self._llave,
                algorithms=["RS256"],
                options={"require": CLAIMS_OBLIGATORIOS},
            )
        except jwt.ExpiredSignatureError:
            raise TokenInvalido("token_vencido")
        except jwt.InvalidSignatureError:
            raise TokenInvalido("firma_invalida")
        except jwt.PyJWTError as e:
            raise TokenInvalido(f"token_invalido:{type(e).__name__}")


def ip_cliente(request) -> str:
    """IP real del cliente (HAProxy agrega X-Forwarded-For)."""
    reenviada = request.headers.get("x-forwarded-for")
    if reenviada:
        return reenviada.split(",")[0].strip()
    return request.client.host if request.client else "desconocida"
