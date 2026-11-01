#!/usr/bin/env python3
"""Auditoría de la RSS por corrida contra Prometheus (desviación encontrada en F5).

Problema: el runner consultó cAdvisor por nombre de contenedor (name="bench-<motor>") y
tomó la primera serie devuelta (res[0]). Cuando el contenedor se recrea, la serie del
contenedor anterior (mismo nombre, otro id) sigue viva unos minutos en Prometheus, así
que la primera serie puede ser la del contenedor viejo. Afecta a:
  - max_rss_mb del CSV            (consulta instantánea max_over_time ... [300s])
  - serie_1s.csv, columna rss_bytes (consulta de rango)
No afecta a cpu_throttled_seconds (sale del cgroup, cpu.stat) ni al resto del CSV.

Esta auditoría NO modifica results/. Para cada corrida vuelve a consultar Prometheus
filtrando por el id del contenedor que midió (meta.json -> identidad.contenedor_id) y
escribe en report/generado/:
  auditoria_rss.csv              una fila por corrida: RSS del CSV frente a la del
                                 contenedor correcto, y si la serie de 1 s coincide
  rss_corregida/<run_id>.csv     serie de 1 s del contenedor correcto, solo para las
                                 corridas cuya serie guardada no era la suya
Requiere Prometheus encendido con los datos de la matriz (retención por defecto: 15 días).
"""

from __future__ import annotations

import argparse
import csv
import json
import urllib.parse
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PROMETHEUS = "http://localhost:9090"
TOL_MIB = 0.15  # el CSV redondea a 0,1 MiB


def consultar(ruta: str, **params) -> list:
    url = f"{PROMETHEUS}/api/v1/{ruta}?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)["data"]["result"]


def es_del_contenedor(serie: dict, cid: str) -> bool:
    return serie["metric"].get("id", "").startswith(f"/docker/{cid}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=RAIZ / "results")
    ap.add_argument("--out", type=Path, default=RAIZ / "report" / "generado")
    a = ap.parse_args()
    dir_corr = a.out / "rss_corregida"
    dir_corr.mkdir(parents=True, exist_ok=True)

    with (a.results / "benchmark_results.csv").open(encoding="utf-8", newline="") as f:
        csv_rss = {(r["timestamp_epoch"], r["engine"], r["profile"], r["query_id"],
                    r["concurrency"], r["run_number"]): r["max_rss_mb"] for r in csv.DictReader(f)}

    filas = []
    for meta in sorted(a.results.glob("ronda_*/runs/*/meta.json")):
        m = json.loads(meta.read_text(encoding="utf-8"))
        fc = m["fila_csv"]
        clave = (str(fc["timestamp_epoch"]), fc["engine"], fc["profile"], fc["query_id"],
                 str(fc["concurrency"]), str(fc["run_number"]))
        cid = m["identidad"]["contenedor_id"]
        nombre = f"bench-{fc['engine']}"
        v = m["ventana"]
        ini, fin = v["t_inicio_medicion"], v["t_fin_medicion"]

        inst = consultar("query", query=f'max_over_time(container_memory_rss{{name="{nombre}"}}'
                         f'[{int(v["medicion_s"])}s])', time=fin)
        propia = [s for s in inst if es_del_contenedor(s, cid)]
        rss_ok = float(propia[0]["value"][1]) / 2**20 if propia else None

        rango = consultar("query_range", query=f'container_memory_rss{{name="{nombre}"}}',
                          start=ini, end=fin, step="1s")
        serie_propia = next((s for s in rango if es_del_contenedor(s, cid)), None)
        guardada = {}
        with (meta.parent / "serie_1s.csv").open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                if r["rss_bytes"]:
                    guardada[int(r["t_epoch"])] = int(r["rss_bytes"])
        coincide = None
        if serie_propia:
            correcta = {round(float(t)): int(float(x)) for t, x in serie_propia["values"]}
            comunes = set(correcta) & set(guardada)
            coincide = bool(comunes) and all(correcta[t] == guardada[t] for t in comunes) \
                and len(guardada) >= len(correcta) - 2
            if not coincide:
                with (dir_corr / f"{m['run_id']}.csv").open("w", encoding="utf-8", newline="") as f:
                    w = csv.writer(f, lineterminator="\n")
                    w.writerow(["t_epoch", "t_rel_s", "rss_bytes"])
                    for t in sorted(correcta):
                        w.writerow([t, round(t - ini, 1), correcta[t]])

        rss_csv = float(csv_rss[clave]) if csv_rss.get(clave) else None
        filas.append({
            "run_id": m["run_id"], "contenedor_id": cid,
            "series_con_el_nombre": len(rango),
            "max_rss_mb_csv": rss_csv,
            "max_rss_mb_contenedor": None if rss_ok is None else round(rss_ok, 1),
            "csv_correcto": None if rss_ok is None or rss_csv is None
            else abs(rss_csv - rss_ok) <= TOL_MIB,
            "serie_1s_correcta": coincide,
        })

    with (a.out / "auditoria_rss.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(filas)
    malas_csv = [f for f in filas if f["csv_correcto"] is False]
    malas_serie = [f for f in filas if f["serie_1s_correcta"] is False]
    sin_dato = [f for f in filas if f["csv_correcto"] is None or f["serie_1s_correcta"] is None]
    print(f"{len(filas)} corridas | max_rss_mb incorrecto: {len(malas_csv)} | "
          f"serie_1s incorrecta: {len(malas_serie)} | sin dato para auditar: {len(sin_dato)}")
    for f in malas_csv:
        print(f"  CSV  {f['run_id']}: {f['max_rss_mb_csv']} -> {f['max_rss_mb_contenedor']}")
    for f in malas_serie:
        print(f"  SERIE {f['run_id']}")
    for f in sin_dato:
        print(f"  SIN DATO {f['run_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
