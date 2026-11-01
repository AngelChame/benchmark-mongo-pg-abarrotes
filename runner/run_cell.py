#!/usr/bin/env python3
"""Una corrida del benchmark: un motor, un perfil, una consulta, una concurrencia.

Protocolo (DOC1 §5.5, D-006, D-009, D-012, D-017, D-021, D-022):
  1. Precondiciones: corriente conectada, servicio MongoDB de Windows detenido,
     espacio libre (Windows y VM) por encima del mínimo.
  2. Recrear el contenedor del motor con los límites del perfil y verificar
     identidad (versión y dato propio del contenedor) y cgroup.
  3. Flush de caché (drop_caches en la VM).
  4. N clientes en lazo cerrado (un proceso por cliente, sin pausa entre consultas),
     con calentamiento, medición y enfriamiento. Solo cuentan las consultas que
     empiezan y terminan dentro de la ventana de medición; las que siguen en vuelo
     al cerrar la ventana se cancelan y no se cuentan.
  5. Métricas: throughput, P50/P95/P99, throttling CFS (cgroup cpu.stat), RSS máximo
     (cAdvisor vía Prometheus), bytes temporales/volcado, OOM (memory.events).

Uso (prueba corta, no cuenta como medición):
    .venv/Scripts/python runner/run_cell.py --engine postgres --profile PROFILE_C_SEVERE \\
        --query Q2 --concurrency 2 --run-number 1 --warmup 5 --measure 15 --cooldown 3 \
        --results-dir <carpeta> --evidence-index <carpeta>/INDEX.md
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runner import config, consultas, contenedores, host, identidad, salidas  # noqa: E402

PROMETHEUS = "http://127.0.0.1:9090"
TIMEOUT_S = 300.0          # D-017: valor inicial; se fija con el piloto
MARGEN_ARRANQUE_S = 5.0    # tiempo para que los clientes se conecten antes de empezar


# --------------------------------------------------------------------------
# Cliente (un proceso por cliente)
# --------------------------------------------------------------------------
def _clasificar_error(exc: Exception) -> str:
    nombre = type(exc).__name__
    if nombre in ("ExecutionTimeout", "QueryCanceled"):
        return "timeout_o_cancelada"
    codigo = getattr(exc, "code", None)
    if codigo == 292 or "QueryExceededMemoryLimit" in str(exc):
        return "limite_memoria"
    if nombre in ("AutoReconnect", "ServerSelectionTimeoutError", "NetworkTimeout",
                  "OperationalError", "ConnectionFailure", "NotPrimaryError"):
        return "conexion"
    return f"error:{nombre}"


def cliente(motor: str, query_id: str, id_cliente: int, t_inicio: float, t_fin: float,
            timeout_s: float, cola) -> None:
    registros = []
    try:
        if motor == "mongo":
            from pymongo import MongoClient
            cli = MongoClient(config.MONGO_URI, maxPoolSize=1)
            if cli.address[1] != config.MONGO_PORT:
                raise identidad.ServidorIncorrecto(f"puerto {cli.address[1]}")
            db = cli[config.BASE]
            ejecutar = lambda: consultas.ejecutar_mongo(db, query_id, timeout_s)  # noqa: E731
        else:
            import psycopg
            conn = psycopg.connect(config.PG_DSN, autocommit=True)
            if conn.info.port != config.PG_PORT:
                raise identidad.ServidorIncorrecto(f"puerto {conn.info.port}")
            consultas.preparar_sesion_pg(conn, query_id, timeout_s)
            ejecutar = lambda: consultas.ejecutar_pg(conn, query_id)  # noqa: E731
    except Exception as e:  # noqa: BLE001
        cola.put((id_cliente, [(None, None, "conexion", repr(e)[:300])]))
        return
    espera = t_inicio - time.time()
    if espera > 0:
        time.sleep(espera)
    while time.time() < t_fin:
        t0 = time.time()
        try:
            ejecutar()
            registros.append((t0, time.time(), "ok", ""))
        except Exception as e:  # noqa: BLE001
            registros.append((t0, time.time(), _clasificar_error(e), repr(e)[:300]))
            if motor == "postgres" and conn.closed:
                try:
                    conn = psycopg.connect(config.PG_DSN, autocommit=True)
                    consultas.preparar_sesion_pg(conn, query_id, timeout_s)
                except Exception:  # noqa: BLE001
                    time.sleep(1)
    cola.put((id_cliente, registros))


# --------------------------------------------------------------------------
# Contadores del contenedor y de los motores
# --------------------------------------------------------------------------
def leer_cgroup(motor: str) -> dict | None:
    r = subprocess.run(["docker", "exec", config.CONTENEDOR[motor], "sh", "-c",
                        "cat /sys/fs/cgroup/cpu.stat; cat /sys/fs/cgroup/memory.events; "
                        "echo memory_peak $(cat /sys/fs/cgroup/memory.peak)"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    datos = {}
    for linea in r.stdout.splitlines():
        partes = linea.split()
        if len(partes) == 2 and partes[1].lstrip("-").isdigit():
            datos[partes[0]] = int(partes[1])
    return datos


def estado_contenedor(motor: str) -> dict:
    r = subprocess.run(["docker", "inspect", "--format",
                        "{{json .State}}", config.CONTENEDOR[motor]], capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 else {}


def contadores_motor(motor: str) -> dict | None:
    """Volcado a disco acumulado del motor (bytes y eventos)."""
    try:
        if motor == "mongo":
            cli, _ = identidad.conectar_mongo()
            q = cli.admin.command("serverStatus")["metrics"]["query"]
            cli.close()
            return {"spill_bytes": int(q["sort"]["spillToDiskBytes"])
                    + int(q["group"]["spilledDataStorageSize"]),
                    "spill_eventos": int(q["sort"]["spillToDisk"]) + int(q["group"]["spills"])}
        conn, _ = identidad.conectar_postgres()
        t = consultas.temporales_pg(conn)
        conn.close()
        return {"spill_bytes": t["temp_bytes"], "spill_eventos": t["temp_files"]}
    except Exception:  # noqa: BLE001
        return None


def cancelar_en_vuelo(motor: str) -> int:
    try:
        if motor == "mongo":
            cli, _ = identidad.conectar_mongo()
            ops = cli.admin.aggregate([{"$currentOp": {}},
                                       {"$match": {"command.comment": consultas.COMENTARIO}}])
            n = 0
            for op in ops:
                cli.admin.command("killOp", op=op["opid"])
                n += 1
            cli.close()
            return n
        conn, _ = identidad.conectar_postgres()
        with conn.cursor() as cur:
            cur.execute("SELECT count(pg_cancel_backend(pid)) FROM pg_stat_activity "
                        "WHERE application_name = 'benchmark' AND state = 'active' "
                        "AND pid <> pg_backend_pid()")
            n = cur.fetchone()[0]
        conn.close()
        return int(n)
    except Exception:  # noqa: BLE001
        return -1


def prometheus(consulta: str, t: float) -> float | None:
    url = f"{PROMETHEUS}/api/v1/query?" + urllib.parse.urlencode({"query": consulta, "time": t})
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            res = json.load(r)["data"]["result"]
        return float(res[0]["value"][1]) if res else None
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# Métricas
# --------------------------------------------------------------------------
def percentil(valores: list[float], p: float) -> float | None:
    """Percentil por rango más cercano (nearest-rank)."""
    if not valores:
        return None
    orden = sorted(valores)
    return orden[max(0, math.ceil(p / 100 * len(orden)) - 1)]


def correr(motor: str, perfil: str, query_id: str, concurrencia: int,
           warmup_s: float, medicion_s: float, enfriamiento_s: float,
           timeout_s: float = TIMEOUT_S, permitir_mongodb_windows: bool = False,
           min_libre_gb: float = host.MIN_LIBRE_GB) -> dict:
    """Ejecuta una corrida completa y devuelve resultados, metadatos y latencias crudas."""
    eventos: list[tuple[float, str]] = []
    # Antes de recrear: corriente, servicio MongoDB y espacio en C: (la VM se revisa después).
    pre = host.verificar_precondiciones(motor, permitir_mongodb_windows, min_libre_gb)
    contenedores.compose("--profile", "telemetry", "up", "-d")
    ident = contenedores.encender_solo(motor, perfil, recrear=True)
    pre_post = host.verificar_precondiciones(motor, permitir_mongodb_windows, min_libre_gb)
    host.drop_caches()
    eventos.append((time.time(), "flush de caché"))

    ctx = mp.get_context("spawn")
    cola = ctx.Queue()
    t_inicio = time.time() + MARGEN_ARRANQUE_S + 0.2 * concurrencia
    t_med_ini = t_inicio + warmup_s
    t_med_fin = t_med_ini + medicion_s
    procesos = [ctx.Process(target=cliente, args=(motor, query_id, i, t_inicio, t_med_fin,
                                                  timeout_s, cola), daemon=True)
                for i in range(concurrencia)]
    for p in procesos:
        p.start()

    # Vigilancia de la corriente durante la corrida (D-012).
    desconexiones: list[float] = []
    fin_vigilancia = threading.Event()

    def vigilar():
        while not fin_vigilancia.wait(5):
            if not host.corriente_conectada():
                desconexiones.append(time.time())

    threading.Thread(target=vigilar, daemon=True).start()

    time.sleep(max(0.0, t_med_ini - time.time()))
    cg_ini, motor_ini = leer_cgroup(motor), contadores_motor(motor)
    eventos.append((time.time(), "inicio de medición"))
    time.sleep(max(0.0, t_med_fin - time.time()))
    cg_fin = leer_cgroup(motor)
    eventos.append((time.time(), "fin de medición"))
    canceladas = cancelar_en_vuelo(motor)

    resultados: dict[int, list] = {}
    limite = time.time() + 60
    while len(resultados) < concurrencia and time.time() < limite:
        try:
            i, regs = cola.get(timeout=max(0.1, limite - time.time()))
            resultados[i] = regs
        except Exception:  # noqa: BLE001
            break
    for p in procesos:
        p.join(timeout=5)
        if p.is_alive():
            p.terminate()
    time.sleep(2)   # estadísticas de procesos paralelos de PostgreSQL
    motor_fin = contadores_motor(motor)
    t_fin_real = time.time()
    estado = estado_contenedor(motor)
    cg_final = leer_cgroup(motor) or cg_fin

    eventos.append((time.time(), "enfriamiento"))
    time.sleep(enfriamiento_s)
    fin_vigilancia.set()

    # ---- consolidación ----
    crudas = []
    for i, regs in sorted(resultados.items()):
        for t0, t1, st, det in regs:
            if t0 is None:
                fase = "sin_conexion"
            elif t0 < t_med_ini:
                fase = "calentamiento"
            elif t1 <= t_med_fin:
                fase = "medicion"
            else:
                fase = "en_vuelo"
            crudas.append({"cliente": i, "t_inicio": t0, "t_fin": t1,
                           "latencia_ms": None if t0 is None else round((t1 - t0) * 1000, 3),
                           "fase": fase, "estado": st, "detalle": det})
    medidas = [c for c in crudas if c["fase"] == "medicion"]
    ok = [c["latencia_ms"] for c in medidas if c["estado"] == "ok"]
    errores = [c for c in crudas if c["estado"] != "ok" and c["fase"] != "en_vuelo"]

    thr_s = None
    if cg_ini and cg_fin:
        thr_s = (cg_fin["throttled_usec"] - cg_ini["throttled_usec"]) / 1e6
    oom_kill = (cg_final or {}).get("oom_kill", 0) - (cg_ini or {}).get("oom_kill", 0) \
        if cg_ini else None
    oom = bool(estado.get("OOMKilled")) or (oom_kill or 0) > 0 or not estado.get("Running", False)
    # Bytes temporales: solo valen si ambos contadores se leyeron y no hubo una caída del
    # motor en medio (PostgreSQL descarta sus estadísticas al recuperarse de un OOM kill).
    temp_no_medido = None
    if not (motor_ini and motor_fin):
        temp_no_medido = "no se pudieron leer los contadores del motor (¿en recuperación tras OOM?)"
    elif motor == "postgres" and (oom_kill or 0) > 0:
        temp_no_medido = "hubo OOM kill: PostgreSQL reinicia sus estadísticas al recuperarse"
    elif motor_fin["spill_bytes"] < motor_ini["spill_bytes"]:
        temp_no_medido = "el contador retrocedió (estadísticas reiniciadas)"
    spill_bytes = None if temp_no_medido else motor_fin["spill_bytes"] - motor_ini["spill_bytes"]
    spill = None if spill_bytes is None else spill_bytes > 0     # None: no medido, nunca estimado
    disco_vm = prometheus(f"sum(increase(node_disk_written_bytes_total[{int(medicion_s)}s]))", t_med_fin)
    rss = prometheus(f'max_over_time(container_memory_rss{{name="{config.CONTENEDOR[motor]}"}}'
                     f'[{int(medicion_s)}s])', t_med_fin)
    thr_prom = prometheus(f'increase(container_cpu_cfs_throttled_seconds_total'
                          f'{{name="{config.CONTENEDOR[motor]}"}}[{int(medicion_s)}s])', t_med_fin)

    if oom:
        status = "oom"
    elif errores:
        status = "error"
    elif spill:
        status = "spill"
    else:
        status = "ok"

    fila = {
        "timestamp_epoch": int(t_med_ini),
        "engine": motor,
        "profile": perfil,
        "query_id": query_id,
        "concurrency": concurrencia,
        "throughput_qps": round(len(ok) / medicion_s, 4),
        "latency_p50_ms": percentil(ok, 50),
        "latency_p95_ms": percentil(ok, 95),
        "latency_p99_ms": percentil(ok, 99),
        "cpu_throttled_seconds": round(thr_s, 3) if thr_s is not None else None,
        "max_rss_mb": round(rss / 2**20, 1) if rss is not None else None,
        "disk_spill": spill,
        "status": status,
    }
    errores_por_tipo: dict[str, int] = {}
    for c in errores:
        errores_por_tipo[c["estado"]] = errores_por_tipo.get(c["estado"], 0) + 1
    meta = {
        "precondiciones": pre, "precondiciones_tras_recrear": pre_post,
        "identidad": ident,
        "ventana": {"t_inicio_calentamiento": t_inicio, "t_inicio_medicion": t_med_ini,
                    "t_fin_medicion": t_med_fin, "t_fin_recoleccion": t_fin_real,
                    "calentamiento_s": warmup_s, "medicion_s": medicion_s,
                    "enfriamiento_s": enfriamiento_s, "timeout_s": timeout_s},
        "variante": config.VARIANTES.get(query_id, {}).get(motor, {}),
        "consultas": {"medidas_ok": len(ok), "medidas_total": len(medidas),
                      "calentamiento": sum(c["fase"] == "calentamiento" for c in crudas),
                      "en_vuelo_canceladas": sum(c["fase"] == "en_vuelo" for c in crudas),
                      "errores_por_tipo": errores_por_tipo,
                      "clientes_que_reportaron": len(resultados),
                      "cancelaciones_enviadas": canceladas},
        "temporales": {"bytes": spill_bytes,
                       "eventos": None if temp_no_medido
                       else motor_fin["spill_eventos"] - motor_ini["spill_eventos"],
                       "no_medido": temp_no_medido,
                       "escritura_disco_vm_bytes": disco_vm,
                       "nota_escritura_disco_vm": "Node Exporter, todos los dispositivos de la VM, "
                                                  "ventana de medición; comprobación cruzada, no "
                                                  "exclusiva del motor",
                       "nota": "PostgreSQL: pg_stat_database.temp_bytes; Mongo: spillToDiskBytes + "
                               "group.spilledDataStorageSize; desde el inicio de la medición hasta "
                               "el fin de la recolección"},
        "cgroup": {"inicio_medicion": cg_ini, "fin_medicion": cg_fin, "final": cg_final,
                   "oom_kill_delta": oom_kill},
        "prometheus": {"max_rss_bytes": rss, "throttled_increase_s": thr_prom},
        "contenedor_estado": {k: estado.get(k) for k in ("Running", "OOMKilled", "ExitCode",
                                                         "StartedAt", "FinishedAt")},
        "corriente_desconexiones": desconexiones,
        "eventos": eventos,
    }
    return {"fila": fila, "meta": meta, "crudas": crudas,
            "descartar": ("corriente desconectada durante la corrida" if desconexiones else None)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--engine", choices=["mongo", "postgres"], required=True)
    ap.add_argument("--profile", choices=config.PERFILES, required=True)
    ap.add_argument("--query", choices=list(consultas.MEDIDAS), required=True)
    ap.add_argument("--concurrency", type=int, required=True)
    ap.add_argument("--warmup", type=float, default=60)
    ap.add_argument("--measure", type=float, default=300)
    ap.add_argument("--cooldown", type=float, default=60)
    ap.add_argument("--timeout", type=float, default=TIMEOUT_S)
    ap.add_argument("--min-free-gb", type=float, default=host.MIN_LIBRE_GB)
    ap.add_argument("--permitir-mongodb-windows", action="store_true",
                    help="solo para pruebas de desarrollo; nunca en el piloto ni en la matriz")
    ap.add_argument("--run-number", type=int, required=True)
    ap.add_argument("--intento", type=int, default=1, help="2 = reintento tras un descarte (D-007)")
    ap.add_argument("--results-dir", type=Path, default=config.RAIZ / "results")
    ap.add_argument("--evidence-index", type=Path, default=config.RAIZ / "evidence" / "INDEX.md")
    args = ap.parse_args()
    sal = salidas.Salidas(args.results_dir, args.evidence_index)
    desc = (f"{args.engine} {args.profile} {args.query} c{args.concurrency} "
            f"r{args.run_number} intento {args.intento}")
    sal.evento("inicio corrida", desc)
    try:
        res = correr(args.engine, args.profile, args.query, args.concurrency, args.warmup,
                     args.measure, args.cooldown, args.timeout, args.permitir_mongodb_windows,
                     args.min_free_gb)
    except (identidad.ServidorIncorrecto, host.PrecondicionFallida) as e:
        sal.evento("corrida abortada antes de medir", f"{desc}: {e}")
        print(f"ABORTADO: {e}", file=sys.stderr)
        return 2
    r = sal.guardar_corrida(res, args.run_number, args.intento)
    print(json.dumps({"run_id": r["run_id"], "carpeta": str(r["carpeta"]),
                      "fila": r["fila"], "motivos": r["motivos"],
                      "descartada": r["descartada"], "archivos": r["archivos"]},
                     indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
