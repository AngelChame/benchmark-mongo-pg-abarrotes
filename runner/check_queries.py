#!/usr/bin/env python3
"""Ejecuta Q1, Q1_CONTEO, Q2 y Q2_RELAXED en ambos motores y compara resultados.

Criterios (D-016): mismos grupos en Q1; conteo de artículos por grupo exacto;
sumas y promedios de Q1 con tolerancia de 1 centavo; lista ordenada de Q2
idéntica (_id y total) en ambos motores y en ambas variantes. No mide nada.

Uso:
    .venv/Scripts/python runner/check_queries.py [--profile PROFILE_A_BASELINE] [--out archivo.json]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runner import config, consultas, contenedores, identidad  # noqa: E402

TOLERANCIA = 0.01   # pesos (1 centavo)
CONSULTAS = ("Q1", "Q1_CONTEO", "Q2", "Q2_RELAXED")


def _normalizar(motor: str, query_id: str, filas: list):
    if query_id == "Q1":
        if motor == "mongo":
            return {(f["_id"]["tipo_pago"], f["_id"]["proveedor_id"]):
                    (float(f["suma_subtotal"]), float(f["promedio_subtotal"])) for f in filas}
        return {(t, p): (float(s), float(a)) for t, p, s, a in filas}
    if query_id == "Q1_CONTEO":
        if motor == "mongo":
            return {(f["_id"]["tipo_pago"], f["_id"]["proveedor_id"]): int(f["articulos"]) for f in filas}
        return {(t, p): int(n) for t, p, n in filas}
    docs = filas if motor == "mongo" else [f[0] for f in filas]
    return [(d["_id"], round(float(d["total"]), 2)) for d in docs]


def correr_motor(motor: str, perfil: str) -> dict:
    info = contenedores.encender_solo(motor, perfil)
    salida = {"identidad": info, "consultas": {}}
    if motor == "mongo":
        cliente, _ = identidad.conectar_mongo()
        db = cliente[config.BASE]
    else:
        conn, _ = identidad.conectar_postgres()
    for q in CONSULTAS:
        t0 = time.perf_counter()
        if motor == "mongo":
            filas = consultas.ejecutar_mongo(db, q)
        else:
            consultas.preparar_sesion_pg(conn, q)
            filas = consultas.ejecutar_pg(conn, q)
        seg = time.perf_counter() - t0
        salida["consultas"][q] = {"segundos": round(seg, 3), "filas": len(filas),
                                  "datos": _normalizar(motor, q, filas)}
        print(f"[{motor}] {q}: {len(filas)} filas en {seg:.2f} s", flush=True)
    (cliente if motor == "mongo" else conn).close()
    contenedores.apagar(motor)
    return salida


def comparar(m: dict, p: dict) -> dict:
    r = {}
    q1m, q1p = m["consultas"]["Q1"]["datos"], p["consultas"]["Q1"]["datos"]
    r["q1_mismos_grupos"] = set(q1m) == set(q1p)
    r["q1_grupos"] = len(q1m)
    comunes = set(q1m) & set(q1p)
    r["q1_dif_max_suma"] = max((abs(q1m[k][0] - q1p[k][0]) for k in comunes), default=None)
    r["q1_dif_max_promedio"] = max((abs(q1m[k][1] - q1p[k][1]) for k in comunes), default=None)
    r["q1_dentro_tolerancia"] = (r["q1_mismos_grupos"] and r["q1_dif_max_suma"] <= TOLERANCIA
                                 and r["q1_dif_max_promedio"] <= TOLERANCIA)
    r["q1_conteos_exactos"] = m["consultas"]["Q1_CONTEO"]["datos"] == p["consultas"]["Q1_CONTEO"]["datos"]
    r["q1_articulos_total"] = sum(m["consultas"]["Q1_CONTEO"]["datos"].values())
    for q in ("Q2", "Q2_RELAXED"):
        r[f"{q.lower()}_identica_entre_motores"] = m["consultas"][q]["datos"] == p["consultas"][q]["datos"]
    r["q2_igual_entre_variantes"] = (m["consultas"]["Q2"]["datos"] == m["consultas"]["Q2_RELAXED"]["datos"]
                                     and p["consultas"]["Q2"]["datos"] == p["consultas"]["Q2_RELAXED"]["datos"])
    r["q2_filas"] = len(m["consultas"]["Q2"]["datos"])
    r["q2_primero"] = m["consultas"]["Q2"]["datos"][0] if m["consultas"]["Q2"]["datos"] else None
    r["todo_ok"] = all([r["q1_dentro_tolerancia"], r["q1_conteos_exactos"],
                        r["q2_identica_entre_motores"], r["q2_relaxed_identica_entre_motores"],
                        r["q2_igual_entre_variantes"], r["q2_filas"] == 1000])
    return r


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", default="PROFILE_A_BASELINE")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    try:
        res = {"mongo": correr_motor("mongo", args.profile),
               "postgres": correr_motor("postgres", args.profile)}
    except identidad.ServidorIncorrecto as e:
        print(e, file=sys.stderr)
        return 2
    comp = comparar(res["mongo"], res["postgres"])
    informe = {
        "fecha_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "perfil": args.profile,
        "identidad": {k: v["identidad"] for k, v in res.items()},
        "tiempos_s": {k: {q: v["consultas"][q]["segundos"] for q in CONSULTAS} for k, v in res.items()},
        "comparacion": comp,
    }
    print(json.dumps(informe, ensure_ascii=False, indent=2))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(informe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if comp["todo_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
