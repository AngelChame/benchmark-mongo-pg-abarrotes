# Planes de ejecución

Generados con `runner/explain_plans.py --out evidence/plans --queries Q1 Q1_WM64 Q2 Q2_RELAXED`
con el perfil `PROFILE_A_BASELINE` (fecha y versiones en `resumen.json`).

| Archivo | Contenido |
|---|---|
| `mongo_<Q>.json` | `explain` con `verbosity: "executionStats"` (Q2 con `allowDiskUse: false`; Q2_RELAXED con `true`) |
| `postgres_<Q>.json` | `EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS, FORMAT JSON)` |
| `postgres_<Q>.txt` | El mismo EXPLAIN en texto (segunda ejecución; los tiempos difieren un poco del JSON) |
| `resumen.json` | Indicadores por plan: método de ordenamiento, disco, bloques temporales, bytes temporales |

Variantes de PostgreSQL (fijadas solo en la sesión con `SET`, sin cambiar la base):
- `Q1`: `work_mem` por defecto (4MB). Es la variante que se mide en la matriz (D-022).
- `Q1_WM64`: `work_mem = 64MB`. Solo es evidencia; no se mide.
- `Q2`: `work_mem = 4MB`; `Q2_RELAXED`: `work_mem = 64MB` (D-008).

`temp_bytes_delta` y `temp_files_delta` son la diferencia de `pg_stat_database` alrededor
de la ejecución en JSON. `temp_written_blocks` es el contador `Temp Written Blocks` del nodo
raíz, en bloques de 8 KB. En Q1 con 4MB es mayor que `temp_bytes_delta` (201 090 bloques,
unos 1,6 GB, frente a 0,84 GB). Hipótesis no verificada: cuenta también las reescrituras
de las pasadas de mezcla (merge).
`resumen.json` también guarda `work_mem_nueva_sesion` y `pg_db_role_setting_filas`, que
confirman que los `SET` no cambiaron la configuración de la base.
