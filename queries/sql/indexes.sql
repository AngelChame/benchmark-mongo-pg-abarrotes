-- Índices de PostgreSQL (D-017): PK en id (ya creada) y B-Tree sobre el texto ISO de fecha.
-- Con collation C, el orden del texto ISO-8601 UTC coincide con el orden cronológico.
CREATE INDEX ventas_fecha_idx ON ventas ((data->>'fecha'));
VACUUM (ANALYZE) ventas;
