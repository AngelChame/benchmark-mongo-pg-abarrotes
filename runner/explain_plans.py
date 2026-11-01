#!/usr/bin/env python3
"""Guarda los planes de ejecución de Q1, Q2 y Q2_RELAXED en ambos motores.

- Mongo: explain con verbosity "executionStats" (con allowDiskUse según la variante).
- PostgreSQL: EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS) en JSON y en texto,
  con work_mem según la variante.
Además resume los indicadores de volcado a disco que fija D-008 (volcado,
archivos temporales o método de ordenamiento distinto entre variantes).

Uso:
    .venv/Scripts/python runner/explain_plans.py --out evidence/plans [--profile PROFILE_A_BASELINE]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bson import json_util  # noqa: E402

from runner import config, consultas, contenedores, identidad  # noqa: E402


def _buscar(nodo, clave):
    """Todos los valores de `clave` en un árbol JSON."""
    if isinstance(nodo, dict):
        for k, v in nodo.items():
            if k == clave:
                yield v
            yield from _buscar(v, clave)
    elif isinstance(nodo, list):
        for x in nodo:
            yield from _buscar(x, clave)


def resumen_mongo(plan: dict) -> dict:
    sorts = [n for n in _buscar(plan, "executionStages")] + [plan]
    return {
        "usedDisk": any(bool(v) for v in _buscar(plan, "usedDisk")),
        "spills": max([int(v) for v in _buscar(plan, "spills") if isinstance(v, (int, float))] or [0]),
        "totalDocsExamined": max([int(v) for v in _buscar(plan, "totalDocsExamined")] or [0]),
        "etapas_pipeline": [list(e.keys())[0] for e in plan.get("stages", []) if isinstance(e, dict)],
        "etapas_ejecucion": sorted(set(_buscar(plan, "stage"))),
        "sort_bytes_max": max([int(v) for n in sorts for v in _buscar(n, "totalDataSizeSorted")] or [0]),
    }


def resumen_pg(plan: list) -> dict:
    raiz = plan[0]
    return {
        "sort_method": sorted(set(_buscar(raiz, "Sort Method"))),
        "sort_space_type": sorted(set(_buscar(raiz, "Sort Space Type"))),
        # Los contadores de buffers son acumulados hacia la raíz: se toma el máximo.
        "temp_written_blocks": max([int(v) for v in _buscar(raiz, "Temp Written Blocks")] or [0]),
        "nodos": sorted(set(_buscar(raiz, "Node Type"))),
        "workers_launched": max([int(v) for v in _buscar(raiz, "Workers Launched")] or [0]),
        "execution_time_ms": raiz.get("Execution Time"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--profile", default="PROFILE_A_BASELINE")
    ap.add_argument("--queries", nargs="+", default=list(consultas.MEDIDAS))
    ap.add_argument("--engines", nargs="+", choices=["mongo", "postgres"],
                    default=["mongo", "postgres"])
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    indice = {"fecha_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "perfil": args.profile, "planes": {}}
    try:
        for motor in args.engines:
            indice.setdefault("identidad", {})[motor] = contenedores.encender_solo(motor, args.profile)
            if motor == "mongo":
                cliente, _ = identidad.conectar_mongo()
                for q in [q for q in args.queries if consultas.ARCHIVOS[q][0]]:
                    plan = consultas.explain_mongo(cliente[config.BASE], q)
                    (args.out / f"mongo_{q}.json").write_text(
                        json_util.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
                    indice["planes"][f"mongo_{q}"] = resumen_mongo(json.loads(json_util.dumps(plan)))
                cliente.close()
            else:
                conn, _ = identidad.conectar_postgres()
                for q in args.queries:
                    plan_json, plan_txt, extra = consultas.explain_pg(conn, q)
                    (args.out / f"postgres_{q}.json").write_text(
                        json.dumps(plan_json, indent=2, ensure_ascii=False), encoding="utf-8")
                    (args.out / f"postgres_{q}.txt").write_text(plan_txt + "\n", encoding="utf-8")
                    indice["planes"][f"postgres_{q}"] = {**resumen_pg(plan_json), **extra}
                conn.close()
                # Comprobación: los SET fueron solo de sesión; la base no cambió.
                conn, _ = identidad.conectar_postgres()
                with conn.cursor() as cur:
                    cur.execute("SELECT current_setting('work_mem'), "
                                "(SELECT count(*) FROM pg_db_role_setting)")
                    wm, n = cur.fetchone()
                indice["work_mem_nueva_sesion"] = wm
                indice["pg_db_role_setting_filas"] = int(n)
                conn.close()
            contenedores.apagar(motor)
    except identidad.ServidorIncorrecto as e:
        print(e, file=sys.stderr)
        return 2
    (args.out / "resumen.json").write_text(json.dumps(indice, indent=2, ensure_ascii=False) + "\n",
                                           encoding="utf-8")
    print(json.dumps(indice["planes"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
