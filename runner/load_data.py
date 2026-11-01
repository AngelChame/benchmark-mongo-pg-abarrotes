#!/usr/bin/env python3
"""Carga el dataset en MongoDB y PostgreSQL y crea los índices del PDF (D-017).

Solo hay un motor encendido a la vez. La carga es preparación: no se mide.
El mismo NDJSON entra en ambos motores; en PostgreSQL la columna `id` es el
uuid5 de `_id` (synthetic_data_generator.uuid_de_venta).

Uso:
    python runner/load_data.py --engine all
    python runner/load_data.py --engine postgres --profile PROFILE_A_BASELINE
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from synthetic_data_generator import uuid_de_venta  # noqa: E402

CONTENEDOR = {"mongo": "bench-mongo", "postgres": "bench-postgres"}
PSQL = ["docker", "exec", "-i", "bench-postgres", "psql", "-X", "-q", "-v",
        "ON_ERROR_STOP=1", "-U", "bench", "-d", "benchmark"]
MONGOSH = ["docker", "exec", "-i", "bench-mongo", "mongosh", "--quiet", "benchmark"]


def sh(cmd: list[str], entrada: str | None = None) -> str:
    r = subprocess.run(cmd, input=entrada, capture_output=True, text=True,
                       encoding="utf-8", cwd=RAIZ)
    if r.returncode != 0:
        raise SystemExit(f"Falló: {' '.join(cmd)}\n{r.stdout}\n{r.stderr}")
    return r.stdout.strip()


def compose(*args: str, perfil: str | None = None) -> None:
    cmd = ["docker", "compose"]
    if perfil:
        cmd += ["--env-file", f"profiles/{perfil}.env"]
    sh(cmd + list(args))


def encender_solo(motor: str, perfil: str) -> None:
    otro = "postgres" if motor == "mongo" else "mongo"
    compose("--profile", otro, "stop", otro)
    compose("--profile", motor, "up", "-d", "--wait", motor, perfil=perfil)


def cargar_mongo(archivo: Path) -> dict:
    t0 = time.perf_counter()
    with archivo.open("rb") as f:
        r = subprocess.run(
            ["docker", "exec", "-i", "bench-mongo", "mongoimport", "--quiet",
             "--db", "benchmark", "--collection", "ventas", "--drop",
             "--numInsertionWorkers", "4"],
            stdin=f, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"mongoimport falló:\n{r.stderr}")
    t_carga = time.perf_counter() - t0
    t0 = time.perf_counter()
    sh(MONGOSH, (RAIZ / "queries/mongo/indexes.js").read_text(encoding="utf-8"))
    t_idx = time.perf_counter() - t0
    info = json.loads(sh(MONGOSH + ["--eval", """
        const s = db.ventas.stats();
        print(JSON.stringify({
          documentos: db.ventas.countDocuments({}),
          indices: db.ventas.getIndexes().map(i => i.name),
          tamano_medio_bson_bytes: s.avgObjSize,
          tamano_datos_bytes: s.size,
          tamano_almacenado_bytes: s.storageSize,
          tamano_indices_bytes: s.totalIndexSize,
          version: db.version()
        }));"""]))
    info.update(segundos_carga=round(t_carga, 1), segundos_indices=round(t_idx, 1))
    return info


def _filas_copy(archivo: Path):
    with archivo.open(encoding="utf-8") as f:
        for linea in f:
            linea = linea.rstrip("\n")
            id_venta = json.loads(linea)["_id"]
            # Formato texto de COPY: la barra invertida es carácter de escape.
            yield f"{uuid_de_venta(id_venta)}\t{linea.replace(chr(92), chr(92) * 2)}\n"


def cargar_postgres(archivo: Path) -> dict:
    sh(PSQL, (RAIZ / "queries/sql/schema.sql").read_text(encoding="utf-8"))
    t0 = time.perf_counter()
    p = subprocess.Popen(PSQL + ["-c", "COPY ventas (id, data) FROM STDIN"],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    lote: list[str] = []
    for fila in _filas_copy(archivo):
        lote.append(fila)
        if len(lote) == 5000:
            p.stdin.write("".join(lote).encode("utf-8"))
            lote.clear()
    p.stdin.write("".join(lote).encode("utf-8"))
    p.stdin.close()
    err = p.stderr.read().decode("utf-8", "replace")
    if p.wait() != 0:
        raise SystemExit(f"COPY falló:\n{err}")
    t_carga = time.perf_counter() - t0
    t0 = time.perf_counter()
    sh(PSQL, (RAIZ / "queries/sql/indexes.sql").read_text(encoding="utf-8"))
    t_idx = time.perf_counter() - t0
    fila = sh(PSQL + ["-At", "-c", """
        SELECT json_build_object(
          'documentos', (SELECT count(*) FROM ventas),
          'indices', (SELECT json_agg(indexname ORDER BY indexname) FROM pg_indexes
                      WHERE tablename = 'ventas'),
          'tamano_medio_jsonb_bytes', (SELECT round(avg(pg_column_size(data)), 1) FROM ventas),
          'tamano_tabla_bytes', pg_table_size('ventas'),
          'tamano_indices_bytes', pg_indexes_size('ventas'),
          'collation', (SELECT datcollate FROM pg_database WHERE datname = 'benchmark'),
          'version', current_setting('server_version'))"""])
    info = json.loads(fila)
    info.update(segundos_carga=round(t_carga, 1), segundos_indices=round(t_idx, 1))
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--engine", choices=["mongo", "postgres", "all"], default="all")
    ap.add_argument("--file", type=Path, default=RAIZ / "data/ventas.ndjson")
    ap.add_argument("--profile", default="PROFILE_A_BASELINE",
                    help="perfil con el que se levanta el motor durante la carga")
    ap.add_argument("--out", type=Path, default=RAIZ / "results/carga.json")
    args = ap.parse_args()
    if not args.file.exists():
        raise SystemExit(f"No existe {args.file}; corre antes synthetic_data_generator.py")

    resultado = json.loads(args.out.read_text(encoding="utf-8")) if args.out.exists() else {}
    motores = ["mongo", "postgres"] if args.engine == "all" else [args.engine]
    for motor in motores:
        print(f"[{motor}] encendiendo con {args.profile} y cargando {args.file.name} ...", flush=True)
        encender_solo(motor, args.profile)
        info = cargar_mongo(args.file) if motor == "mongo" else cargar_postgres(args.file)
        info.update(perfil_carga=args.profile,
                    fecha_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        resultado[motor] = info
        print(json.dumps(info, ensure_ascii=False, indent=2), flush=True)
        compose("--profile", motor, "stop", motor)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(resultado, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
