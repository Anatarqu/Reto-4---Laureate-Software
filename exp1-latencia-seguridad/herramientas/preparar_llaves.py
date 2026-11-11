"""Genera, una sola vez, el certificado TLS de HAProxy y las llaves del JWT.

  certs/tls.pem          certificado autofirmado + llave (HAProxy los quiere juntos)
  claves/jwt_priv.pem    llave privada RS256 del Proveedor de Identidad
  claves/jwt_pub.pem     llave pública con la que el gateway y Respuestas validan

Uso:  docker compose run --rm herramientas python herramientas/preparar_llaves.py
"""
import datetime
import ipaddress
import os
import sys

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CERTS = os.path.join(BASE, "certs")
CLAVES = os.path.join(BASE, "claves")


def escribir(ruta: str, datos: bytes) -> None:
    with open(ruta, "wb") as f:
        f.write(datos)
    os.chmod(ruta, 0o644)   # HAProxy corre sin privilegios y debe poder leerlo
    print("  creado", os.path.relpath(ruta, BASE))


def llave_rsa():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def pem_privada(llave) -> bytes:
    return llave.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def main() -> None:
    os.makedirs(CERTS, exist_ok=True)
    os.makedirs(CLAVES, exist_ok=True)
    if os.path.exists(os.path.join(CLAVES, "jwt_priv.pem")) and "--forzar" not in sys.argv:
        print("Las llaves ya existen (usa --forzar para regenerarlas).")
        return

    print("Certificado TLS para HAProxy:")
    llave_tls = llave_rsa()
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    ahora = datetime.datetime.now(datetime.timezone.utc)
    certificado = (
        x509.CertificateBuilder()
        .subject_name(nombre)
        .issuer_name(nombre)
        .public_key(llave_tls.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - datetime.timedelta(minutes=5))
        .not_valid_after(ahora + datetime.timedelta(days=90))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.DNSName("balanceador"),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            ]),
            critical=False,
        )
        .sign(llave_tls, hashes.SHA256())
    )
    escribir(
        os.path.join(CERTS, "tls.pem"),
        certificado.public_bytes(serialization.Encoding.PEM) + pem_privada(llave_tls),
    )

    print("Llaves del JWT (RS256):")
    llave_jwt = llave_rsa()
    escribir(os.path.join(CLAVES, "jwt_priv.pem"), pem_privada(llave_jwt))
    escribir(
        os.path.join(CLAVES, "jwt_pub.pem"),
        llave_jwt.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ),
    )
    print("Listo.")


if __name__ == "__main__":
    main()
