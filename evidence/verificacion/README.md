# Verificación F2 (antes del piloto)

Fecha: 2026-11-01T05:59:43Z a 2026-11-01T06:01:15Z (UTC). Código: commit `ad5e6ddffa91`.  
Generado con `runner/verificar_f2.py`, que se versiona en el mismo commit que esta evidencia (el commit indicado es el HEAD al ejecutar). Ninguna de estas ejecuciones es una medición.

**Resultado: TODAS LAS CONDICIONES SE CUMPLEN**

| Condición | Resultado | Detalle |
|---|---|---|
| 500 000 ventas en MongoDB | cumple | 500,000 |
| 500 000 ventas en PostgreSQL | cumple | 500,000 |
| ≥ 3 niveles de anidamiento | cumple | 4 (items → producto → proveedor → campos) |
| ~3 artículos por venta | cumple | Mongo 3.0013; PostgreSQL 3.0013 |
| Tamaño por venta ≥ 0.85 KB | cumple | JSON 940.2 B (0.940 kB; 0.918 KiB); BSON 984 B; JSONB 1174.6 B |
| Mismo archivo en ambos motores (muestra de documentos) | cumple | 1998/1998 documentos idénticos |
| Q1: mismos grupos | cumple | 90 grupos |
| Q1: conteo de artículos por grupo exacto | cumple | 674,686 artículos |
| Q1: sumas y promedios con tolerancia de 1 centavo | cumple | dif. máx. suma 9.31e-10; promedio 2.84e-14 |
| Q2: Top-1000 idéntico entre motores | cumple | 1000 filas; primero ('VNT324763', 2732.63) |
| Q2_RELAXED: idéntico entre motores | cumple |  |
| Q1 y Q2 terminan en el Perfil A con 1 cliente | cumple | mongo Q1 2.06 s, Q1_CONTEO 1.05 s, Q2 0.28 s, Q2_RELAXED 0.26 s; postgres Q1 3.44 s, Q1_CONTEO 2.19 s, Q2 0.25 s, Q2_RELAXED 0.17 s |
| Límites reales A/B/C = perfil, en ambos motores | cumple | mongo_PROFILE_A_BASELINE: 4096 MiB, swap 0, cpu 400000 100000; postgres_PROFILE_A_BASELINE: 4096 MiB, swap 0, cpu 400000 100000; mongo_PROFILE_B_MEDIUM: 2048 MiB, swap 0, cpu 200000 100000; postgres_PROFILE_B_MEDIUM: 2048 MiB, swap 0, cpu 200000 100000; mongo_PROFILE_C_SEVERE: 1024 MiB, swap 0, cpu 100000 100000; postgres_PROFILE_C_SEVERE: 1024 MiB, swap 0, cpu 100000 100000 |
| drop_caches funciona en la VM | cumple | Cached 850348 kB → 374868 kB |

## Notas

- Tolerancia y comparación según D-016: conteos exactos, sumas y promedios con tolerancia de 1 centavo (Mongo suma en double; PostgreSQL en numeric), lista de Q2 idéntica (`_id` y `total`).
- "Terminan con 1 cliente": cada consulta se ejecutó una vez, de forma secuencial, con el Perfil A; los tiempos son de una sola ejecución y no son mediciones.
- El tamaño por venta se toma del JSON canónico generado (el mismo archivo para ambos motores); también se reportan el BSON de Mongo y el JSONB de PostgreSQL.
- Servicio MongoDB de Windows durante F2: Running (no afecta la verificación: todo se conecta al contenedor con verificación de identidad, D-021).
- Los planes de Q1 y Q2 están en `evidence/plans/` y no se repiten aquí.

## Archivos

- `documentos.json`: conteos, índices, tamaños, hash del dataset y muestra comparada
- `consultas.json`: comparación completa, tiempos e identidad de los servidores
- `q1_grupos.csv`: los 90 grupos de Q1 con los valores de ambos motores y sus diferencias
- `q2_top1000.csv`, `q2_relaxed_top1000.csv`: las 1000 filas de ambos motores, lado a lado
- `limites.json`: memory.max, memory.swap.max y cpu.max reales por perfil y motor
- `drop_caches.json`: salida de la prueba de flush en la VM
