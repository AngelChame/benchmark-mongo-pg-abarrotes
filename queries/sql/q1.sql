-- Q1 (CPU): desanida items, filtra precio_unitario > 50, calcula cantidad * precio_unitario
-- y agrupa por tipo_pago y proveedor del artículo con suma y promedio.
SELECT data->>'tipo_pago'                                         AS tipo_pago,
       item->'producto'->'proveedor'->>'id'                       AS proveedor_id,
       SUM((item->>'cantidad')::numeric * (item->>'precio_unitario')::numeric) AS suma_subtotal,
       AVG((item->>'cantidad')::numeric * (item->>'precio_unitario')::numeric) AS promedio_subtotal
FROM ventas,
     jsonb_array_elements(data->'items') AS item
WHERE (item->>'precio_unitario')::numeric > 50
GROUP BY 1, 2
