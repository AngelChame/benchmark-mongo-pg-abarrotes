-- Q2 (memoria): ventas no fiadas desde 2026-01-01, ordenadas por total descendente
-- (desempate por folio ascendente), Top-1000. Sin índice en total.
-- La variante de memoria se fija por sesión: SET work_mem = '4MB' (Q2) o '64MB' (Q2_RELAXED).
SELECT data
FROM ventas
WHERE (data->>'es_fiado')::boolean = false
  AND data->>'fecha' >= '2026-01-01T00:00:00Z'
ORDER BY (data->>'total')::numeric DESC,
         (data->>'folio')::int ASC
LIMIT 1000
