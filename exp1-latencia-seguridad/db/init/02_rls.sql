-- Row-Level Security (tácticas: limitar el acceso y separar entidades).
-- Cada transacción fija la institución con:  SELECT set_config('app.tenant_id', '<tenant>', true)
-- Si no se fija, current_setting(..., true) devuelve NULL y la consulta no ve ninguna fila.

ALTER TABLE intentos   ENABLE ROW LEVEL SECURITY;
ALTER TABLE respuestas ENABLE ROW LEVEL SECURITY;

CREATE POLICY aislamiento_institucion ON intentos
    USING      (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY aislamiento_institucion ON respuestas
    USING      (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
