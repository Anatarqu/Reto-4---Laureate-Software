-- BD de Intentos (vista de latencia) y tabla de logs de seguridad (vista de seguridad).

-- Rol de la aplicación: NO es dueño de las tablas ni superusuario, así que RLS siempre aplica.
CREATE ROLE app_eval LOGIN PASSWORD 'app_eval';
-- Rol del consumidor de logs: solo puede insertar y leer en log_seguridad.
CREATE ROLE app_logs LOGIN PASSWORD 'app_logs';

CREATE TABLE intentos (
    id            uuid PRIMARY KEY,
    tenant_id     text NOT NULL,
    estudiante_id text NOT NULL,
    estado        text NOT NULL DEFAULT 'abierto'
);

CREATE TABLE respuestas (
    tenant_id   text        NOT NULL,
    intento_id  uuid        NOT NULL,
    pregunta_id text        NOT NULL,
    valor       text,
    seq         bigint      NOT NULL,
    answer_id   text,
    actualizado timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (intento_id, pregunta_id)
);

CREATE TABLE log_seguridad (
    id        bigserial PRIMARY KEY,
    fecha     timestamptz NOT NULL,
    servicio  text,
    usuario   text,
    tenant_id text,
    ip        text,
    recurso   text,
    metodo    text,
    motivo    text
);

GRANT SELECT, INSERT, UPDATE ON intentos, respuestas TO app_eval;
GRANT SELECT, INSERT ON log_seguridad TO app_logs;
GRANT USAGE ON SEQUENCE log_seguridad_id_seq TO app_logs;
