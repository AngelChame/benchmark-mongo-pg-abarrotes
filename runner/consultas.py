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
    "Q1_WM64": (None, "q1.sql"),                          # solo explain en PostgreSQL (D-022)
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


def temporales_pg(conn) -> dict:
    """Contadores acumulados de archivos temporales de la base (pg_stat_database).

    Se fuerza el envío de las estadísticas pendientes de esta sesión antes de
    leer; los procesos paralelos envían las suyas al terminar.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT pg_stat_force_next_flush()")
        cur.execute("SELECT 1")
        cur.execute("SELECT pg_stat_clear_snapshot()")
        cur.execute("SELECT temp_files, temp_bytes FROM pg_stat_database "
                    "WHERE datname = current_database()")
        archivos, bytes_ = cur.fetchone()
    return {"temp_files": int(archivos), "temp_bytes": int(bytes_)}


def explain_mongo(db, query_id: str) -> dict:
    cmd = {"aggregate": config.COLECCION, "pipeline": pipeline(query_id), "cursor": {}}
    cmd.update(opciones_mongo(query_id))
    return db.command({"explain": cmd, "verbosity": "executionStats"})


def explain_pg(conn, query_id: str) -> tuple[list, str, dict]:
    """EXPLAIN (ANALYZE, BUFFERS) en JSON y en texto, con la variante aplicada.

    Devuelve también los archivos y bytes temporales de la ejecución en JSON
    (diferencia de pg_stat_database alrededor de esa única ejecución).
    """
    import time
    preparar_sesion_pg(conn, query_id)
    antes = temporales_pg(conn)
    with conn.cursor() as cur:
        cur.execute("EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS, FORMAT JSON) " + sql(query_id))
        plan_json = cur.fetchone()[0]
        cur.execute("SHOW work_mem")
        work_mem = cur.fetchone()[0]
    time.sleep(1.5)   # los procesos paralelos envían sus estadísticas al terminar
    despues = temporales_pg(conn)
    with conn.cursor() as cur:
        cur.execute("EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS) " + sql(query_id))
        plan_txt = "\n".join(r[0] for r in cur.fetchall())
    extra = {"work_mem_sesion": work_mem,
             "temp_files_delta": despues["temp_files"] - antes["temp_files"],
             "temp_bytes_delta": despues["temp_bytes"] - antes["temp_bytes"]}
    return plan_json, plan_txt, extra
