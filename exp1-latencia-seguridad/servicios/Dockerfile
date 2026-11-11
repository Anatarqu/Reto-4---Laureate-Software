# Imagen única para los servicios del experimento 1 (gateway, respuestas, persistidor,
# logs de seguridad e IdP). Cada servicio cambia solo el comando en docker-compose.yml.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
