-- Esquema de PostgreSQL (DOC1): ventas(id UUID PRIMARY KEY, data JSONB)
-- La base `benchmark` se crea con collation C (POSTGRES_INITDB_ARGS en docker-compose.yml).
DROP TABLE IF EXISTS ventas;
CREATE TABLE ventas (
    id   UUID PRIMARY KEY,      -- uuid5 determinista calculado desde data->>'_id'
    data JSONB NOT NULL
);
