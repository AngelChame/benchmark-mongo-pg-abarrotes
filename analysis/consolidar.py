#!/usr/bin/env python3
"""Consolida la matriz (F5/F6): tablas por celda y gráficas para el reporte.

Lee (sin modificar nada):
  results/benchmark_results.csv              una fila por corrida (14 columnas fijas)
  results/ronda_*/runs/<run_id>/meta.json    contadores del cgroup, temporales, consultas
  results/ronda_*/runs/<run_id>/serie_1s.csv RSS y throttling cada segundo

Escribe en report/generado/:
  celdas.csv            resumen por celda (mediana de las rondas; P99 mínimo y máximo)
  cifras.json           las mismas cifras en JSON, para citarlas en el texto
  tabla_rendimiento.tex tabla LaTeX (longtable): estados, QPS y percentiles
  tabla_recursos.tex    tabla LaTeX (longtable): throttling, memoria y temporales
  fig_p99_concurrencia.pdf, fig_throttling_qps.pdf, fig_rss.pdf

Resumen por celda: mediana de las corridas con dato (D-006: 3 rondas). Una corrida OOM
sin consultas terminadas no aporta latencia. Uso:
    .venv/Scripts/python analysis/consolidar.py [--results results] [--out report/generado]
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent

MOTORES = ["mongo", "postgres"]
NOMBRE_MOTOR = {"mongo": "MongoDB", "postgres": "PostgreSQL"}
PERFILES = ["PROFILE_A_BASELINE", "PROFILE_B_MEDIUM", "PROFILE_C_SEVERE"]
CORTO = {"PROFILE_A_BASELINE": "A", "PROFILE_B_MEDIUM": "B", "PROFILE_C_SEVERE": "C"}
LIMITE_MIB = {"PROFILE_A_BASELINE": 4096, "PROFILE_B_MEDIUM": 2048, "PROFILE_C_SEVERE": 1024}
CPUS = {"PROFILE_A_BASELINE": 4, "PROFILE_B_MEDIUM": 2, "PROFILE_C_SEVERE": 1}
CONSULTAS = ["Q1", "Q2", "Q2_RELAXED"]
CONCURRENCIAS = [1, 10, 50]

# Paleta categórica validada (skill dataviz, modo claro): motor = identidad.
COLOR = {"mongo": "#2a78d6", "postgres": "#eb6834"}
MARCA_MOTOR = {"mongo": "o", "postgres": "s"}
MARCA_CONSULTA = {"Q1": "o", "Q2": "^", "Q2_RELAXED": "D"}
TINTA, TINTA2, REJILLA = "#0b0b0b", "#52514e", "#e4e3df"


def num(v: str):
    return None if v in ("", None) else float(v)


def mediana(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def leer_auditoria(out: Path) -> dict:
    """RSS del contenedor correcto según auditar_rss.py, solo donde el CSV no coincide."""
    archivo = out / "auditoria_rss.csv"
    if not archivo.exists():
        return {}
    with archivo.open(encoding="utf-8", newline="") as f:
        return {r["run_id"]: float(r["max_rss_mb_contenedor"]) for r in csv.DictReader(f)
                if r["csv_correcto"] == "False" and r["max_rss_mb_contenedor"]}


def leer_corridas(results: Path, out: Path) -> list[dict]:
    auditoria = leer_auditoria(out)
    with (results / "benchmark_results.csv").open(encoding="utf-8", newline="") as f:
        filas = list(csv.DictReader(f))
    metas = {}
    for meta in results.glob("ronda_*/runs/*/meta.json"):
        m = json.loads(meta.read_text(encoding="utf-8"))
        metas[m["run_id"]] = (m, meta.parent)
    corridas = []
    for f in filas:
        run_id = (f"{f['timestamp_epoch']}_{f['engine']}_{f['profile']}_{f['query_id']}"
                  f"_c{f['concurrency']}_r{f['run_number']}")
        if run_id not in metas:
            raise SystemExit(f"falta meta.json de {run_id}")
        m, carpeta = metas[run_id]
        cg = m["cgroup"]
        d_per = cg["fin_medicion"]["nr_periods"] - cg["inicio_medicion"]["nr_periods"]
        d_thr = cg["fin_medicion"]["nr_throttled"] - cg["inicio_medicion"]["nr_throttled"]
        tb = m["temporales"]["bytes"]
        corridas.append({
            "run_id": run_id, "carpeta": carpeta,
            "engine": f["engine"], "profile": f["profile"], "query_id": f["query_id"],
            "concurrency": int(f["concurrency"]), "run_number": int(f["run_number"]),
            "qps": num(f["throughput_qps"]), "p50": num(f["latency_p50_ms"]),
            "p95": num(f["latency_p95_ms"]), "p99": num(f["latency_p99_ms"]),
            "throttled_s": num(f["cpu_throttled_seconds"]),
            "rss_mb": auditoria.get(run_id, num(f["max_rss_mb"])),
            "rss_corregida": run_id in auditoria,
            "disk_spill": f["disk_spill"], "status": f["status"],
            "pct_periodos": 100.0 * d_thr / d_per if d_per > 0 else None,
            "memory_peak_mib": cg["fin_medicion"].get("memory_peak", 0) / 2**20,
            "temp_gib": None if tb is None else tb / 2**30,
            "temp_no_medido": m["temporales"]["no_medido"],
            "oom_kill": cg["oom_kill_delta"],
            "ok_medidas": m["consultas"]["medidas_ok"],
            "errores": sum(m["consultas"].get("errores_por_tipo", {}).values()),
        })
    return corridas


def resumir(corridas: list[dict]) -> list[dict]:
    celdas = {}
    for c in corridas:
        celdas.setdefault((c["engine"], c["profile"], c["query_id"], c["concurrency"]), []).append(c)
    orden = {q: i for i, q in enumerate(CONSULTAS)}
    filas = []
    for (e, p, q, n), cs in sorted(celdas.items(), key=lambda kv: (
            orden[kv[0][2]], kv[0][0], PERFILES.index(kv[0][1]), kv[0][3])):
        cs.sort(key=lambda c: c["run_number"])
        p99s = [c["p99"] for c in cs if c["p99"] is not None]
        estados = {}
        for c in cs:
            estados[c["status"]] = estados.get(c["status"], 0) + 1
        filas.append({
            "engine": e, "profile": p, "query_id": q, "concurrency": n, "rondas": len(cs),
            "estados": " ".join(f"{v}x{k}" for k, v in sorted(estados.items())),
            "qps": mediana([c["qps"] for c in cs]),
            "p50": mediana([c["p50"] for c in cs]), "p95": mediana([c["p95"] for c in cs]),
            "p99": mediana(p99s), "p99_min": min(p99s) if p99s else None,
            "p99_max": max(p99s) if p99s else None,
            "throttled_s": mediana([c["throttled_s"] for c in cs]),
            "pct_periodos": mediana([c["pct_periodos"] for c in cs]),
            "rss_mb_max": max(c["rss_mb"] for c in cs),
            "rss_corregidas": sum(c["rss_corregida"] for c in cs),
            "memory_peak_mib_max": max(c["memory_peak_mib"] for c in cs),
            "temp_gib": mediana([c["temp_gib"] for c in cs]),
            "temp_medidas": sum(c["temp_gib"] is not None for c in cs),
            "ok_medidas_min": min(c["ok_medidas"] for c in cs),
            "ok_medidas_mediana": mediana([c["ok_medidas"] for c in cs]),
            "oom_kill_total": sum(c["oom_kill"] for c in cs),
            "errores_total": sum(c["errores"] for c in cs),
        })
    return filas


# ---------- LaTeX ----------

def fmt(x, dec=0, vacio="---"):
    if x is None:
        return vacio
    s = f"{x:,.{dec}f}"
    return s.replace(",", "\\,").replace(".", "{,}")


def fmt_ms(x):
    if x is None:
        return "---"
    return fmt(x, 0) if x >= 100 else fmt(x, 1)


def nombre_q(q):
    return q.replace("_", "\\_")


def estados_tex(s):
    return s.replace("x", "$\\times$")


def tabla(filas, columnas, titulo, etiqueta, nota):
    cab = " & ".join(c[0] for c in columnas)
    alin = "".join(c[1] for c in columnas)
    out = [f"\\begin{{longtable}}{{{alin}}}",
           f"\\caption{{{titulo}}}\\label{{{etiqueta}}}\\\\", "\\toprule", cab + " \\\\",
           "\\midrule", "\\endfirsthead", "\\toprule", cab + " \\\\", "\\midrule", "\\endhead",
           "\\midrule", f"\\multicolumn{{{len(columnas)}}}{{r}}{{\\footnotesize continúa}}\\\\",
           "\\endfoot", "\\bottomrule",
           f"\\multicolumn{{{len(columnas)}}}{{p{{0.95\\linewidth}}}}{{\\footnotesize {nota}}}\\\\",
           "\\endlastfoot"]
    previo = None
    for f in filas:
        if previo and previo != (f["query_id"], f["engine"]):
            out.append("\\addlinespace")
        previo = (f["query_id"], f["engine"])
        out.append(" & ".join(c[2](f) for c in columnas) + " \\\\")
    out.append("\\end{longtable}")
    return "\n".join(out) + "\n"


def pocas(f):
    # D-007: con menos de 100 consultas exitosas el P99 (rango más cercano) es el máximo;
    # entre 100 y 999 es orientativo. Se usa la ronda con menos consultas de la celda.
    if f["p99"] is None:
        return ""
    if f["ok_medidas_min"] < 100:
        return "$^{\\dagger}$"
    if f["ok_medidas_min"] < 1000:
        return "$^{\\ddagger}$"
    return ""


def H(*lineas):
    """Encabezado de varias líneas."""
    return "\\begin{tabular}[b]{@{}c@{}}" + "\\\\".join(lineas) + "\\end{tabular}"


CONSULTA_CORTA = {"Q1": "Q1", "Q2": "Q2", "Q2_RELAXED": "Q2R"}
COMUNES = [
    ("", "l", lambda f: CONSULTA_CORTA[f["query_id"]]),
    ("Motor", "l", lambda f: NOMBRE_MOTOR[f["engine"]]),
    ("Perfil", "c", lambda f: CORTO[f["profile"]]),
    ("$c$", "r", lambda f: str(f["concurrency"])),
]
COL_REND = COMUNES + [
    ("Estados", "l", lambda f: estados_tex(f["estados"])),
    ("QPS", "r", lambda f: fmt(f["qps"], 2)),
    (H("P50", "(ms)"), "r", lambda f: fmt_ms(f["p50"])),
    (H("P95", "(ms)"), "r", lambda f: fmt_ms(f["p95"])),
    (H("P99", "(ms)"), "r", lambda f: fmt_ms(f["p99"]) + pocas(f)),
    (H("P99 entre rondas", "mín.--máx. (ms)"), "r", lambda f: "---" if f["p99_min"] is None
     else f"{fmt_ms(f['p99_min'])}--{fmt_ms(f['p99_max'])}"),
]
COL_REC = COMUNES + [
    (H("Throttling", "(s)"), "r", lambda f: fmt(f["throttled_s"], 0)),
    (H("Periodos con", "throttling (\\%)"), "r", lambda f: fmt(f["pct_periodos"], 0)),
    (H("RSS máx.", "(MiB)"), "r", lambda f: fmt(f["rss_mb_max"], 0)
     + ("$^{\\S}$" if f["rss_corregidas"] else "")),
    (H("Pico cgroup", "(MiB)"), "r", lambda f: fmt(f["memory_peak_mib_max"], 0)),
    (H("Temporales", "(GiB)"), "r", lambda f: fmt(f["temp_gib"], 1) if f["temp_medidas"] else
     ("n/m" if f["temp_gib"] is None and f["engine"] == "postgres" and "oom" in f["estados"]
      else "---")),
    (H("Consultas", "ok"), "r", lambda f: fmt(f["ok_medidas_mediana"], 0)),
]


# ---------- Gráficas ----------

def estilo():
    plt.rcParams.update({
        "font.size": 8, "axes.titlesize": 8.5, "axes.labelsize": 8, "legend.fontsize": 7.5,
        "xtick.labelsize": 7.5, "ytick.labelsize": 7.5, "axes.edgecolor": TINTA2,
        "axes.labelcolor": TINTA, "xtick.color": TINTA2, "ytick.color": TINTA2,
        "text.color": TINTA, "axes.grid": True, "grid.color": REJILLA, "grid.linewidth": 0.6,
        "axes.spines.top": False, "axes.spines.right": False, "lines.linewidth": 1.6,
        "pdf.fonttype": 42, "figure.dpi": 150, "savefig.bbox": "tight",
    })


def fig_p99(celdas, out: Path):
    fig, ejes = plt.subplots(2, 3, figsize=(7.2, 4.6), sharex=True)
    idx = {(f["engine"], f["profile"], f["query_id"], f["concurrency"]): f for f in celdas}
    for i, q in enumerate(["Q1", "Q2"]):
        for j, p in enumerate(PERFILES):
            ax = ejes[i][j]
            ax.set_xscale("log")
            ax.set_yscale("log")
            for e in MOTORES:
                xs, ys, lo, hi = [], [], [], []
                for n in CONCURRENCIAS:
                    f = idx.get((e, p, q, n))
                    if f is None:
                        continue
                    if f["p99"] is None:
                        # OOM sin ninguna consulta terminada: no hay latencia que graficar.
                        ax.text(0.97, 0.05, f"{NOMBRE_MOTOR[e]}, {n} clientes:\nOOM, 0 consultas",
                                transform=ax.transAxes, ha="right", va="bottom", fontsize=6.5,
                                color=TINTA2)
                        continue
                    xs.append(n)
                    ys.append(f["p99"])
                    lo.append(f["p99"] - f["p99_min"])
                    hi.append(f["p99_max"] - f["p99"])
                    if "oom" in f["estados"]:
                        ax.annotate("OOM", (n, f["p99"]), xytext=(4, 4), textcoords="offset points",
                                    fontsize=6.5, color=COLOR[e])
                ax.errorbar(xs, ys, yerr=[lo, hi], color=COLOR[e], marker=MARCA_MOTOR[e],
                            markersize=4.5, capsize=2, elinewidth=0.9,
                            markeredgecolor="white", markeredgewidth=0.6)
            ax.set_xticks(CONCURRENCIAS, [str(n) for n in CONCURRENCIAS])
            ax.minorticks_off()
            ax.set_title(f"{q} · Perfil {CORTO[p]} ({CPUS[p]} CPU, {LIMITE_MIB[p] // 1024} GiB)")
            if j == 0:
                ax.set_ylabel("P99 (ms, escala log)")
            if i == 1:
                ax.set_xlabel("clientes concurrentes")
    manejadores = [Line2D([], [], color=COLOR[e], marker=MARCA_MOTOR[e], label=NOMBRE_MOTOR[e])
                   for e in MOTORES]
    fig.legend(handles=manejadores, loc="upper center", ncol=2, frameon=False,
               bbox_to_anchor=(0.5, 1.03))
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(out / "fig_p99_concurrencia.pdf")
    plt.close(fig)


def fig_throttling(corridas, out: Path):
    fig, ejes = plt.subplots(1, 3, figsize=(7.2, 2.7), sharey=True)
    for j, p in enumerate(PERFILES):
        ax = ejes[j]
        ax.set_xscale("log")
        for c in corridas:
            if c["profile"] != p or not c["qps"]:
                continue
            ax.scatter(c["qps"], c["throttled_s"], s=22, color=COLOR[c["engine"]],
                       marker=MARCA_CONSULTA[c["query_id"]], edgecolors="white",
                       linewidths=0.6, zorder=3)
        ax.set_title(f"Perfil {CORTO[p]} (cuota {CPUS[p]} CPU)")
        ax.set_xlabel("QPS (escala log)")
        if j == 0:
            ax.set_ylabel("throttling CFS en la ventana (s)")
    manejadores = ([Line2D([], [], color=COLOR[e], marker="o", ls="", label=NOMBRE_MOTOR[e])
                    for e in MOTORES]
                   + [Line2D([], [], color=TINTA2, marker=MARCA_CONSULTA[q], ls="",
                             label=q.replace("_", " ")) for q in CONSULTAS])
    fig.legend(handles=manejadores, loc="upper center", ncol=5, frameon=False,
               bbox_to_anchor=(0.5, 1.06))
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(out / "fig_throttling_qps.pdf")
    plt.close(fig)


def leer_serie(carpeta: Path, corregidas: Path):
    # Si la auditoría (auditar_rss.py) encontró que la serie guardada era la de otro
    # contenedor, se usa la serie del contenedor correcto.
    archivo = corregidas / f"{carpeta.name}.csv"
    if not archivo.exists():
        archivo = carpeta / "serie_1s.csv"
    t, rss = [], []
    with archivo.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r["rss_bytes"]:
                t.append(float(r["t_rel_s"]))
                rss.append(float(r["rss_bytes"]) / 2**20)
    return t, rss


def fig_rss(corridas, out: Path):
    fig, ejes = plt.subplots(1, 3, figsize=(7.2, 2.6), sharex=True)
    for j, p in enumerate(PERFILES):
        ax = ejes[j]
        for e in MOTORES:
            for c in corridas:
                # Solo la ronda 1, para que se lean las caídas por OOM; las rondas 2 y 3 se
                # comportan igual (ver tabla de recursos).
                if (c["engine"], c["profile"], c["query_id"], c["concurrency"],
                        c["run_number"]) == (e, p, "Q1", 50, 1):
                    t, rss = leer_serie(c["carpeta"], out / "rss_corregida")
                    ax.plot(t, rss, color=COLOR[e], lw=1.2)
        ax.axhline(LIMITE_MIB[p], color=TINTA2, ls="--", lw=0.9)
        ax.text(2, LIMITE_MIB[p], f"memory.max = {LIMITE_MIB[p]} MiB", va="bottom", fontsize=6.5,
                color=TINTA2)
        ax.set_ylim(0, LIMITE_MIB[p] * 1.12)
        ax.set_title(f"Q1, 50 clientes · Perfil {CORTO[p]}")
        ax.set_xlabel("segundos de la ventana de medición")
        if j == 0:
            ax.set_ylabel("RSS del contenedor (MiB)")
    manejadores = [Line2D([], [], color=COLOR[e], label=NOMBRE_MOTOR[e] + " (ronda 1)")
                   for e in MOTORES]
    fig.legend(handles=manejadores, loc="upper center", ncol=2, frameon=False,
               bbox_to_anchor=(0.5, 1.06))
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(out / "fig_rss.pdf")
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=RAIZ / "results")
    ap.add_argument("--out", type=Path, default=RAIZ / "report" / "generado")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    corridas = leer_corridas(a.results, a.out)
    celdas = resumir(corridas)

    campos = list(celdas[0].keys())
    with (a.out / "celdas.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=campos, lineterminator="\n")
        w.writeheader()
        w.writerows(celdas)
    estados = {}
    for c in corridas:
        estados[c["status"]] = estados.get(c["status"], 0) + 1
    (a.out / "cifras.json").write_text(json.dumps({
        "corridas": len(corridas), "celdas": len(celdas), "estados": estados,
        "rondas": sorted({c["run_number"] for c in corridas}),
        "temporales_gib_total": sum(c["temp_gib"] or 0 for c in corridas),
        "celdas_detalle": celdas,
    }, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    nota = ("Q2R: \\texttt{Q2\\_RELAXED}. Mediana de las 3 rondas por celda; P99 mín.--máx. entre rondas. "
            "Percentiles por rango más cercano sobre las consultas exitosas de la ventana (D-007): "
            "$^{\\dagger}$ menos de 100 consultas en alguna ronda, el P99 es igual al máximo; "
            "$^{\\ddagger}$ entre 100 y 999, P99 orientativo. ``---'': sin consultas terminadas.")
    (a.out / "tabla_rendimiento.tex").write_text(tabla(
        celdas, COL_REND, "Rendimiento por celda (ventana de 300 s)", "tab:rendimiento", nota),
        encoding="utf-8")
    nota_rec = ("Q2R: \\texttt{Q2\\_RELAXED}. Mediana de las 3 rondas salvo RSS y pico del cgroup (máximo). Throttling: delta de "
                "\\texttt{throttled\\_usec} de \\texttt{cpu.stat}, que suma el tiempo de todas las CPU "
                "del grupo y puede superar los 300 s de la ventana. Periodos con throttling: "
                "\\texttt{nr\\_throttled}/\\texttt{nr\\_periods}. RSS: cAdvisor "
                "(\\texttt{container\\_memory\\_rss}); pico del cgroup: \\texttt{memory.peak}, que "
                "incluye caché de página y memoria compartida desde el arranque del contenedor. "
                "Temporales: \\texttt{pg\\_stat\\_database.temp\\_bytes} (PostgreSQL) o "
                "\\texttt{spillToDiskBytes} (MongoDB); n/m: no medido (PostgreSQL reinicia sus estadísticas tras un OOM kill).")
    (a.out / "tabla_recursos.tex").write_text(tabla(
        celdas, COL_REC, "Recursos por celda (ventana de 300 s)", "tab:recursos", nota_rec),
        encoding="utf-8")

    estilo()
    fig_p99(celdas, a.out)
    fig_throttling(corridas, a.out)
    fig_rss(corridas, a.out)
    print(f"{len(corridas)} corridas, {len(celdas)} celdas, estados {estados} -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
