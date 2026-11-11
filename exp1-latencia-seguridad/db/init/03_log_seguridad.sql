-- log_seguridad es solo de inserción: un trigger rechaza UPDATE y DELETE.
-- (La limpieza entre corridas usa TRUNCATE con el superusuario; los roles de la
--  aplicación no tienen permiso de TRUNCATE.)

CREATE FUNCTION bloquear_cambios_log() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'log_seguridad es solo de inserción';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER log_seguridad_inmutable
    BEFORE UPDATE OR DELETE ON log_seguridad
    FOR EACH ROW EXECUTE FUNCTION bloquear_cambios_log();
