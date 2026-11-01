# Contención de CPU y memoria en agregaciones JSON: MongoDB frente a PostgreSQL JSONB

Medición de latencia, rendimiento y huella de recursos de MongoDB y PostgreSQL (JSONB)
bajo tres perfiles de contención de CPU y memoria (Docker, cgroups v2).

- Materia: Bases de Datos en la Nube, IT2iD, Universidad Politécnica de Chiapas, 2026.
- Dataset: 500 000 ventas **sintéticas** basadas en el modelo de un proyecto de abarrotes
  (no contiene datos reales).

> Estado: **en construcción.** Este repositorio todavía no contiene código ni resultados.

## Estructura

| Carpeta | Contenido previsto |
|---|---|
| `queries/mongo/` | Consultas Q1 y Q2 para MongoDB (pipelines de agregación) |
| `queries/sql/` | Consultas Q1 y Q2 para PostgreSQL JSONB |
| `runner/` | Runner del benchmark (Python multiproceso) |
| `data/` | Dataset generado (no se versiona; se regenera con semilla fija) |
| `results/` | CSV de resultados y metadatos por corrida |
| `evidence/` | Planes de ejecución y evidencia de volcado a disco u OOM |

Las instrucciones de reproducción se agregarán cuando el entorno esté listo.
