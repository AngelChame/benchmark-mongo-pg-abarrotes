# Contención de CPU y memoria en agregaciones JSON: MongoDB frente a PostgreSQL JSONB

Medición de latencia, rendimiento y huella de recursos de MongoDB 9.0.4 y PostgreSQL 18.6 (JSONB) bajo tres perfiles de límites de Docker (cgroups v2), con 1, 10 y 50 clientes concurrentes.

- Materia: Bases de Datos en la Nube, IT2iD, Universidad Politécnica de Chiapas, 2026.
- Dataset: 500 000 ventas **sintéticas** basadas en el modelo de un proyecto de abarrotes (no contiene datos reales). Semilla fija 20261101.

## Estado

**Experimento terminado.** La matriz completa se ejecutó el 1 de noviembre de 2026: 114 corridas de 300 s (38 celdas × 3 rondas), sin descartes. Resultado por estado: 87 `ok`, 21 `spill` y 6 `oom`; todas las corridas `spill` y `oom` son de PostgreSQL con Q1. El análisis completo está en [`report/reporte.pdf`](report/reporte.pdf) (reporte de 7 secciones, desviaciones y limitaciones).

## Estructura

| Ruta | Contenido |
|---|---|
| `docker-compose.yml`, `profiles/` | Servicios y perfiles de recursos A (4 CPU / 4 GiB), B (2 / 2 GiB) y C (1 / 1 GiB) |
| `synthetic_data_generator.py` | Generador del dataset de 500 000 ventas (determinista) |
| `queries/mongo/`, `queries/sql/` | Q1 y Q2 para MongoDB y PostgreSQL |
| `runner/` | Runner en Python multiproceso, orquestador, verificación del baseline y planes `explain` |
| `run_benchmarks.sh` | Automatiza la matriz (pausar/reanudar entre corridas; con `--respaldo`, respaldos con SHA-256) |
| `telemetry/` | Configuración de Prometheus (cAdvisor y Node Exporter, scrape de 1 s) |
| `analysis/` | Consolidación de resultados, tablas, figuras y auditoría de RSS |
| `results/` | `benchmark_results.csv` (14 columnas, 114 filas), datos por corrida, bitácora y manifiestos |
| `evidence/` | Planes de ejecución, verificación del baseline y del entorno, y evidencia de volcado a disco y OOM (`INDEX.md`) |
| `report/` | Fuente LaTeX y PDF del reporte |
| `data/` | Dataset generado (no se versiona; se regenera con la semilla) |

## Cómo reproducirlo

Requisitos: Docker con cgroups v2 (los resultados se obtuvieron con Docker Desktop sobre WSL2 en Windows), Python 3.12, Git Bash para `run_benchmarks.sh`, y libres en `127.0.0.1` los puertos que publica `docker-compose.yml`: 27018 (MongoDB), 5432 (PostgreSQL), 9090 (Prometheus), 8080 (cAdvisor) y 9100 (Node Exporter). Crear el entorno en `.venv` (lo exige `run_benchmarks.sh`): `python -m venv .venv` y `.venv/Scripts/python -m pip install -r requirements.txt` (en Linux, `.venv/bin/python`); los comandos `python …` de abajo se ejecutan con ese entorno activado.

Desde la raíz del repositorio:

```bash
docker compose --profile telemetry up -d       # Prometheus, cAdvisor y Node Exporter
python synthetic_data_generator.py              # 500 000 ventas, semilla 20261101
python runner/load_data.py                      # carga e índices en ambos motores
python runner/verificar_f2.py                   # verificación del baseline
python runner/explain_plans.py --out evidence/plans --queries Q1 Q1_WM64 Q2 Q2_RELAXED
./run_benchmarks.sh --rondas 3 --timeout 300    # matriz completa (orquestador)
python analysis/auditar_rss.py                  # auditoría de la RSS (con Prometheus)
python analysis/consolidar.py                   # tablas y figuras del reporte
```

La matriz completa tarda unas 15 horas de reloj (la ejecutada: de 01:15 a 16:09, con una pausa de 50 min; unas 14 horas sin contar la pausa). Cada corrida usa 60 s de calentamiento, 300 s de medición y 60 s de enfriamiento, y deja `meta.json`, `latencias.csv` y `serie_1s.csv` en `results/ronda_0N/runs/<id>/`.

## Integridad de los resultados

`results/manifiestos/` contiene un manifiesto SHA-256 por ronda. En un clon, `sha256sum -c results/manifiestos/<ronda_03>.sha256` verifica todo salvo cuatro archivos que el orquestador siguió actualizando después del respaldo (`bitacora.log`, `estado.txt`, `resumen.md`, `orquestador_matriz.out`). Los manifiestos de las rondas 1 y 2 son copias intermedias.

## Limitaciones principales

Docker Desktop sobre WSL2 (no Linux nativo) y cliente de carga en el mismo equipo; dataset adaptado a abarrotes (`es_fiado` en lugar de `completed`, proveedor en lugar de categoría); Q2 no vuelca a disco en ninguno de los dos motores, por lo que `Q2_RELAXED` repite el resultado de Q2. Lista completa en el reporte.
