"""Carga y ejecución de Q1, Q2 y sus variantes en ambos motores.

Los textos de las consultas viven en queries/ (fuente única); aquí solo se
leen y se ejecutan con la variante de memoria que corresponde (D-008).
"""

from __future__ import annotations

import json
from functools import lru_cache

from runner import config

ARCHIVOS = {
    "Q1": ("q1.json", "q1.sql"),
    "Q2": ("q2.json", "q2.sql"),
    "Q2_RELAXED": ("q2.json", "q2.sql"),
    "Q1_CONTEO": ("q1_conteo.json", "q1_conteo.sql"),   # verificación, no se mide
}
MEDIDAS = ("Q1", "Q2", "Q2_RELAXED")


@lru_cache(maxsize=None)
def pipeline(query_id: str) -> list:
    return json.loads((config.RAIZ / "queries/mongo" / ARCHIVOS[query_id][0]).read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def sql(query_id: str) -> str:
    texto = (config.RAIZ / "queries/sql" / ARCHIVOS[query_id][1]).read_text(encoding="utf-8")
    return "\n".join(l for l in texto.splitlines() if not l.lstrip().startswith("--")).strip()


def opciones_mongo(query_id: str) -> dict:
    return dict(config.VARIANTES.get(query_id, {}).get("mongo", {}))


def preparar_sesion_pg(conn, query_id: str, timeout_s: float | None = None) -> None:
    """Fija la variante de memoria (work_mem) y el timeout en la sesión."""
    work_mem = config.VARIANTES.get(query_id, {}).get("postgres", {}).get("work_mem")
    with conn.cursor() as cur:
        cur.execute(f"SET work_mem = '{work_mem}'" if work_mem else "RESET work_mem")
        cur.execute(f"SET statement_timeout = {int(timeout_s * 1000)}" if timeout_s
                    else "RESET statement_timeout")


def ejecutar_mongo(db, query_id: str, timeout_s: float | None = None) -> list:
    kw = opciones_mongo(query_id)
    if timeout_s:
        kw["maxTimeMS"] = int(timeout_s * 1000)
    return list(db[config.COLECCION].aggregate(pipeline(query_id), **kw))


def ejecutar_pg(conn, query_id: str) -> list:
    with conn.cursor() as cur:
        cur.execute(sql(query_id))
        return cur.fetchall()


def explain_mongo(db, query_id: str) -> dict:
    cmd = {"aggregate": config.COLECCION, "pipeline": pipeline(query_id), "cursor": {}}
    cmd.update(opciones_mongo(query_id))
    return db.command({"explain": cmd, "verbosity": "executionStats"})


def explain_pg(conn, query_id: str) -> tuple[list, str]:
    """EXPLAIN (ANALYZE, BUFFERS) en JSON y en texto, con la variante aplicada."""
    preparar_sesion_pg(conn, query_id)
    with conn.cursor() as cur:
        cur.execute("EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS, FORMAT JSON) " + sql(query_id))
        plan_json = cur.fetchone()[0]
        cur.execute("EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS) " + sql(query_id))
        plan_txt = "\n".join(r[0] for r in cur.fetchall())
    return plan_json, plan_txt
