-- Verificación de Q1 (no se mide): número de artículos por grupo, comparación exacta entre motores.
SELECT data->>'tipo_pago'                   AS tipo_pago,
       item->'producto'->'proveedor'->>'id' AS proveedor_id,
       COUNT(*)                             AS articulos
FROM ventas,
     jsonb_array_elements(data->'items') AS item
WHERE (item->>'precio_unitario')::numeric > 50
GROUP BY 1, 2
