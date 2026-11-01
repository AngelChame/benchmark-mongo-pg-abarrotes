"""Salidas de cada corrida (PR-19, D-007, D-009).

Por corrida, en results/runs/<run_id>/:
  meta.json          archivo complementario (fila, metadatos, motivos de error)
  latencias.csv      latencias crudas de cada consulta (todas las fases)
  serie_1s.csv       RSS y throttling del contenedor por segundo en la ventana de medición
  docker_logs.txt    últimas líneas de `docker logs` (solo si status es oom o error)

Archivos acumulados (se agregan líneas con flush y fsync):
  results/benchmark_results.csv   CSV de 14 columnas exactas (DOC2)
  results/descartes.csv           corridas descartadas por causa externa (D-007)
  results/bitacora.log            eventos con fecha y hora (D-009)
  evidence/INDEX.md               una línea por fila spill u oom del CSV
"""

from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from runner import config

# Esquema fijo del CSV (DOC2): sin agregar ni renombrar columnas.
COLUMNAS = ["timestamp_epoch", "engine", "profile", "query_id", "concurrency", "run_number",
            "throughput_qps", "latency_p50_ms", "latency_p95_ms", "latency_p99_ms",
            "cpu_throttled_seconds", "max_rss_mb", "disk_spill", "status"]
ESTADOS = ("ok", "spill", "oom", "error")
PRIORIDAD = {"oom": 3, "error": 2, "spill": 1, "ok": 0}      # D-017
CAMPOS_MEDIDOS = ["throughput_qps", "latency_p50_ms", "latency_p95_ms", "latency_p99_ms",
                  "cpu_throttled_seconds", "max_rss_mb", "disk_spill"]
COLUMNAS_DESCARTES = ["fecha_utc", "run_id", "engine", "profile", "query_id", "concurrency",
                      "run_number", "intento", "motivo"]
LINEAS_LOG = 200
PROMETHEUS = "http://127.0.0.1:9090"


def ahora_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _agregar(ruta: Path, texto: str, encabezado: str | None = None) -> None:
    """Agrega texto al final del archivo con flush y fsync (sobrevive a un corte)."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    nuevo = not ruta.exists() or ruta.stat().st_size == 0
    with ruta.open("a", encoding="utf-8", newline="") as f:
        if nuevo and encabezado:
            f.write(encabezado)
        f.write(texto)
        f.flush()
        os.fsync(f.fileno())


def _linea_csv(valores: list) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerow(valores)
    return buf.getvalue()


def _escribir_json(ruta: Path, datos) -> None:
    ruta.write_text(json.dumps(datos, indent=2, ensure_ascii=False, default=str) + "\n",
                    encoding="utf-8")


class Salidas:
    def __init__(self, dir_resultados: Path | None = None, indice_evidencia: Path | None = None,
                 dir_runs: Path | None = None):
        self.dir = Path(dir_resultados or config.RAIZ / "results")
        self.dir_runs = Path(dir_runs or self.dir / "runs")
        self.csv = self.dir / "benchmark_results.csv"
        self.descartes = self.dir / "descartes.csv"
        self.bitacora = self.dir / "bitacora.log"
        self.indice = Path(indice_evidencia or config.RAIZ / "evidence" / "INDEX.md")

    # ---------------- bitácora ----------------
    def evento(self, tipo: str, detalle: str = "") -> None:
        _agregar(self.bitacora, f"{ahora_utc()} | {tipo} | {detalle}\n")

    # ---------------- corrida ----------------
    @staticmethod
    def run_id(fila: dict, run_number: int) -> str:
        return (f"{fila['timestamp_epoch']}_{fila['engine']}_{fila['profile']}_"
                f"{fila['query_id']}_c{fila['concurrency']}_r{run_number}")

    @staticmethod
    def cerrar_fila(fila: dict, run_number: int) -> tuple[dict, list[str]]:
        """Completa run_number, valida el status y marca error si algo no se midió.

        Los campos que no se pudieron medir quedan vacíos (nunca estimados) y su
        motivo se devuelve para el archivo complementario. Si hay campos sin medir,
        el status pasa a 'error', salvo que ya sea 'oom' (prioridad oom > error).
        """
        fila = {**fila, "run_number": run_number}
        motivos = []
        sin_ok = fila.get("latency_p50_ms") is None
        for campo in CAMPOS_MEDIDOS:
            if fila.get(campo) is None:
                if campo.startswith("latency_") and sin_ok:
                    motivos.append(f"{campo}: ninguna consulta exitosa en la ventana de medición")
                else:
                    motivos.append(f"{campo}: no se pudo medir")
        status = fila.get("status") if fila.get("status") in ESTADOS else "error"
        if fila.get("status") not in ESTADOS:
            motivos.append(f"status inválido o vacío ({fila.get('status')!r})")
        if motivos and PRIORIDAD[status] < PRIORIDAD["error"]:
            status = "error"
        fila["status"] = status
        return fila, motivos

    def guardar_corrida(self, res: dict, run_number: int, intento: int = 1) -> dict:
        """Escribe todos los archivos de una corrida terminada. Devuelve un resumen."""
        fila, motivos = self.cerrar_fila(res["fila"], run_number)
        rid = self.run_id(fila, run_number)
        carpeta = self.dir_runs / rid
        carpeta.mkdir(parents=True, exist_ok=True)
        meta = res["meta"]
        motor = fila["engine"]

        # Latencias crudas.
        with (carpeta / "latencias.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["cliente", "t_inicio_epoch", "t_fin_epoch", "latencia_ms", "fase",
                        "estado", "detalle"])
            for c in res["crudas"]:
                w.writerow([c["cliente"], c["t_inicio"], c["t_fin"], c["latencia_ms"], c["fase"],
                            c["estado"], c["detalle"]])

        # Serie por segundo de RSS y throttling (ventana de medición).
        serie = serie_prometheus(motor, meta["ventana"]["t_inicio_medicion"],
                                 meta["ventana"]["t_fin_medicion"])
        with (carpeta / "serie_1s.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["t_epoch", "t_rel_s", "rss_bytes", "throttled_seconds_total",
                        "throttled_seconds_delta"])
            for r in serie["filas"]:
                w.writerow([r["t"], r["t_rel"], r["rss"], r["thr_total"], r["thr_delta"]])
        if serie["error"]:
            motivos.append(f"serie_1s: {serie['error']}")

        # docker logs en oom o error.
        archivos = ["meta.json", "latencias.csv", "serie_1s.csv"]
        if fila["status"] in ("oom", "error"):
            (carpeta / "docker_logs.txt").write_text(docker_logs(motor), encoding="utf-8")
            archivos.append("docker_logs.txt")

        descartada = res.get("descartar")
        _escribir_json(carpeta / "meta.json", {
            "run_id": rid, "run_number": run_number, "intento": intento,
            "fila_csv": fila, "motivos_error": motivos,
            "descartada": descartada, "serie_1s_muestras": len(serie["filas"]),
            "archivos": archivos, **meta})

        if descartada:
            self.registrar_descarte(rid, fila, run_number, intento, descartada)
        elif clave_fila(fila) in self.claves_csv():
            # Nunca se duplica una fila (misma celda y misma ronda).
            self.evento("fila duplicada evitada", f"{rid}: la celda ya tiene fila en el CSV")
        else:
            _agregar(self.csv, _linea_csv([_valor_csv(fila[c]) for c in COLUMNAS]),
                     _linea_csv(COLUMNAS))
            self.evento("corrida terminada", f"{rid} status={fila['status']}")
            if fila["status"] in ("spill", "oom"):
                self._indexar(fila, rid, carpeta, archivos, meta)
        return {"run_id": rid, "carpeta": carpeta, "fila": fila, "motivos": motivos,
                "descartada": descartada, "archivos": archivos}

    def claves_csv(self) -> set[tuple]:
        """Celdas (engine, profile, query_id, concurrency, run_number) que ya tienen fila."""
        if not self.csv.exists():
            return set()
        with self.csv.open(encoding="utf-8", newline="") as f:
            return {clave_fila(r) for r in csv.DictReader(f)}

    def descartes_por_clave(self) -> dict[tuple, int]:
        """Número de intentos descartados por celda y ronda (D-007)."""
        if not self.descartes.exists():
            return {}
        cuenta: dict[tuple, int] = {}
        with self.descartes.open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                k = clave_fila(r)
                cuenta[k] = cuenta.get(k, 0) + 1
        return cuenta

    def registrar_descarte(self, rid: str, celda: dict, run_number: int, intento: int,
                           motivo: str) -> None:
        _agregar(self.descartes, _linea_csv([
            ahora_utc(), rid, celda["engine"], celda["profile"], celda["query_id"],
            celda["concurrency"], run_number, intento, motivo]), _linea_csv(COLUMNAS_DESCARTES))
        self.evento("descarte", f"{rid} intento {intento}: {motivo}")

    def _indexar(self, fila: dict, rid: str, carpeta: Path, archivos: list[str], meta: dict) -> None:
        rel = Path(os.path.relpath(carpeta, self.indice.parent)).as_posix()
        enlaces = " · ".join(f"[{a}]({rel}/{a})" for a in archivos)
        tmp = meta.get("temporales", {}).get("bytes")
        detalle = f"temporales {tmp / 2**20:.1f} MiB" if tmp else "sin bytes temporales"
        encabezado = ("# Índice de evidencia\n\nUna línea por cada fila `spill` u `oom` del CSV "
                      "(`results/benchmark_results.csv`).\n\n"
                      "| Fila (run_id) | status | Detalle | Archivos |\n|---|---|---|---|\n")
        _agregar(self.indice, f"| `{rid}` | {fila['status']} | {detalle} | {enlaces} |\n", encabezado)


def clave_fila(f: dict) -> tuple:
    return (str(f["engine"]), str(f["profile"]), str(f["query_id"]), int(f["concurrency"]),
            int(f["run_number"]))


def _valor_csv(v):
    if v is None:
        return ""                       # no medido: vacío, nunca estimado
    if isinstance(v, bool):
        return "true" if v else "false"
    return v


def docker_logs(motor: str, lineas: int = LINEAS_LOG) -> str:
    r = subprocess.run(["docker", "logs", "--timestamps", "--tail", str(lineas),
                        config.CONTENEDOR[motor]], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return (f"# docker logs --tail {lineas} {config.CONTENEDOR[motor]} ({ahora_utc()})\n"
            + r.stdout + r.stderr)


def _rango(consulta: str, inicio: float, fin: float) -> list:
    url = f"{PROMETHEUS}/api/v1/query_range?" + urllib.parse.urlencode(
        {"query": consulta, "start": inicio, "end": fin, "step": "1s"})
    with urllib.request.urlopen(url, timeout=20) as r:
        res = json.load(r)["data"]["result"]
    return res[0]["values"] if res else []


def serie_prometheus(motor: str, inicio: float, fin: float) -> dict:
    """RSS y throttling acumulado del contenedor, paso de 1 s, de inicio a fin."""
    nombre = config.CONTENEDOR[motor]
    try:
        rss = {round(float(t)): int(float(v)) for t, v in
               _rango(f'container_memory_rss{{name="{nombre}"}}', inicio, fin)}
        thr = {round(float(t)): float(v) for t, v in
               _rango(f'container_cpu_cfs_throttled_seconds_total{{name="{nombre}"}}', inicio, fin)}
    except Exception as e:  # noqa: BLE001
        return {"filas": [], "error": f"Prometheus no respondió ({e!r})"}
    filas, previo = [], None
    for t in sorted(set(rss) | set(thr)):
        total = thr.get(t)
        delta = round(total - previo, 6) if total is not None and previo is not None else None
        if total is not None:
            previo = total
        filas.append({"t": t, "t_rel": round(t - inicio, 1), "rss": rss.get(t),
                      "thr_total": total, "thr_delta": delta})
    error = None if filas else "Prometheus no devolvió muestras para la ventana"
    return {"filas": filas, "error": error}
