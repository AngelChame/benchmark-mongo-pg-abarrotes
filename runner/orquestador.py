#!/usr/bin/env python3
"""Orquestador de la matriz: rondas, orden aleatorio con semilla, retomar, pausas.

Reglas (D-006, D-007, D-008, D-009, D-012, D-020, D-021, D-022, PR-19):
- Ronda = 38 celdas (36 base + 2 Q2_RELAXED en Perfil C con 50 clientes), en orden
  aleatorio con semilla fija; una corrida a la vez. run_number = número de ronda.
- Retomar: una celda/ronda con fila en el CSV está hecha; nunca se duplica. Una
  corrida cortada a medias (queda en_curso.json) se registra como descarte.
- Descarte (causa externa, p. ej. corriente desconectada): un solo reintento.
- Hora límite: no se inicia una corrida que no alcance a terminar antes.
- PAUSAR / DETENER (archivos en la carpeta de resultados) se revisan entre corridas.
- Revisión del entorno al arrancar y al reanudar; bloqueo de suspensión de Windows.
- Respaldo de results/ y evidence/ al terminar cada ronda, con manifiesto SHA-256.
- Nunca permite el MongoDB de Windows encendido y nunca ejecuta git.

Uso: ver run_benchmarks.sh.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runner import (avisos, config, contenedores, host, identidad,  # noqa: E402
                    run_cell, salidas)

ZONA = ZoneInfo("America/Mexico_City")
SEMILLA_ORDEN = 20261101
MOTORES = ("mongo", "postgres")
CONSULTAS_BASE = ("Q1", "Q2")
CONCURRENCIAS = (1, 10, 50)
EXTRA_RELAXED = [("mongo", "PROFILE_C_SEVERE", "Q2_RELAXED", 50),
                 ("postgres", "PROFILE_C_SEVERE", "Q2_RELAXED", 50)]
SOBRECOSTO_INICIAL_S = 180.0   # recrear contenedor, flush, recolección (se ajusta con lo observado)
ESPERA_S = 30.0                # sondeo en pausa / esperando corriente


class Abortar(RuntimeError):
    """Condición que impide seguir: el orquestador se detiene sin medir."""


def ahora_local() -> str:
    return datetime.now(ZONA).strftime("%Y-%m-%d %H:%M:%S %Z")


# --------------------------------------------------------------------------
# Celdas y orden
# --------------------------------------------------------------------------
def celdas_ronda() -> list[dict]:
    base = [(m, p, q, c) for m in MOTORES for p in config.PERFILES
            for q in CONSULTAS_BASE for c in CONCURRENCIAS]
    return [dict(zip(("engine", "profile", "query_id", "concurrency"), t))
            for t in base + EXTRA_RELAXED]


def orden_ronda(celdas: list[dict], semilla: int, ronda: int) -> list[dict]:
    lista = sorted(celdas, key=lambda c: (c["engine"], c["profile"], c["query_id"], c["concurrency"]))
    random.Random(f"{semilla}:ronda:{ronda}").shuffle(lista)
    return lista


def parsear_celda(texto: str) -> dict:
    """'postgres:C:Q1:50' o 'postgres:PROFILE_C_SEVERE:Q1:50'."""
    motor, perfil, consulta, conc = texto.split(":")
    if len(perfil) == 1:
        perfil = {"A": "PROFILE_A_BASELINE", "B": "PROFILE_B_MEDIUM", "C": "PROFILE_C_SEVERE"}[perfil.upper()]
    celda = {"engine": motor, "profile": perfil, "query_id": consulta, "concurrency": int(conc)}
    if motor not in MOTORES or perfil not in config.PERFILES or \
            consulta not in ("Q1", "Q2", "Q2_RELAXED"):
        raise argparse.ArgumentTypeError(f"celda inválida: {texto}")
    return celda


def clave(celda: dict, ronda: int) -> tuple:
    return (celda["engine"], celda["profile"], celda["query_id"], int(celda["concurrency"]), ronda)


def texto_celda(celda: dict) -> str:
    return f"{celda['engine']} {celda['profile']} {celda['query_id']} c{celda['concurrency']}"


# --------------------------------------------------------------------------
# Sistema real (Docker, Windows)
# --------------------------------------------------------------------------
class SistemaReal:
    def corriente(self) -> bool:
        return host.corriente_conectada()

    def docker_ok(self) -> bool:
        return subprocess.run(["docker", "info"], capture_output=True).returncode == 0

    def impedir_suspension(self, activar: bool) -> bool:
        return host.impedir_suspension(activar)

    def revisar_entorno(self, momento: str, min_libre_gb: float) -> dict:
        """D-009: contenedores, límites del cgroup, espacio, corriente, servicio MongoDB,
        suspensión. Lanza Abortar si algo impide medir; devuelve avisos no bloqueantes."""
        info: dict = {"momento": momento, "avisos": []}
        if not self.docker_ok():
            raise Abortar("Docker no responde (docker info falló).")
        susp = host.revisar_suspension()
        info["suspension"] = susp
        info["plan_energia"] = host.plan_energia()
        if susp["problemas"]:
            raise Abortar("El equipo conectado puede suspenderse: " + "; ".join(susp["problemas"])
                          + ". Corrígelo en PowerShell como administrador con: "
                          + " ; ".join(susp["comandos_correccion"]))
        for c in susp["no_comprobado"]:
            info["avisos"].append(f"No se pudo comprobar con powercfg: {c}"
                                  + (" (no cierres la tapa durante la matriz)" if c == "accion_tapa" else ""))
        info["modo_energia"] = host.modo_energia()
        if "error" in info["modo_energia"]:
            info["avisos"].append(f"No se pudo leer el modo de energía: {info['modo_energia']['error']}")
        elif not info["modo_energia"]["maximo_rendimiento"]:
            info["avisos"].append(f"Modo de energía en corriente: {info['modo_energia']['modo_ac']}; "
                                  "D-009 pide máximo rendimiento.")
        contenedores.compose("--profile", "telemetry", "up", "-d")
        for motor in MOTORES:
            ident = contenedores.encender_solo(motor, "PROFILE_C_SEVERE", recrear=True)
            pre = host.verificar_precondiciones(motor, False, min_libre_gb)
            if motor == "mongo":
                cli, _ = identidad.conectar_mongo()
                docs = cli[config.BASE][config.COLECCION].estimated_document_count()
                cli.close()
            else:
                conn, _ = identidad.conectar_postgres()
                with conn.cursor() as cur:
                    cur.execute("SELECT count(*) FROM ventas")
                    docs = cur.fetchone()[0]
                conn.close()
            if docs != 500_000:
                raise Abortar(f"{motor}: hay {docs} documentos; se esperaban 500 000.")
            info[motor] = {"identidad": ident, "precondiciones": pre, "documentos": docs}
            contenedores.apagar(motor)
        return info


# --------------------------------------------------------------------------
# Orquestador
# --------------------------------------------------------------------------
class Orquestador:
    def __init__(self, args, ejecutor=run_cell.correr, sistema=None, dormir=time.sleep):
        self.a = args
        self.ejecutar = ejecutor
        self.sis = sistema or SistemaReal()
        self.dormir = dormir
        self.dir = Path(args.results_dir or config.RAIZ / "results").resolve()
        principal = (config.RAIZ / "results").resolve()
        self.indice = (config.RAIZ / "evidence" / "INDEX.md") if self.dir == principal \
            else self.dir / "evidence_INDEX.md"
        self.sal = salidas.Salidas(self.dir, self.indice)
        self.en_curso = self.dir / "en_curso.json"
        self.f_pausar, self.f_detener = self.dir / "PAUSAR", self.dir / "DETENER"
        self.limite = args.hora_limite
        self.celdas = args.celdas or celdas_ronda()
        self.sobrecosto = SOBRECOSTO_INICIAL_S
        self.estado = "iniciando"
        self.ultima: dict | None = None
        self.siguiente: str = ""
        self.avisos_entorno: list[str] = []
        self.avisos_fijos: list[str] = []
        self.sin_escritura = False
        self.corridas_sesion = 0
        self.respaldo_pendiente = False
        self.respaldos_fallidos: list[dict] = []

    # ---------- utilidades ----------
    def log(self, tipo: str, detalle: str = "", aviso: bool = False) -> None:
        if self.sin_escritura:            # modo --revisar-entorno: solo pantalla
            print(f"[{ahora_local()}] {tipo}: {detalle}", flush=True)
            return
        self.sal.evento(tipo, detalle)
        print(f"[{ahora_local()}] {tipo}: {detalle}", flush=True)
        if aviso:
            avisos.avisar(f"Benchmark: {tipo}", detalle or tipo)

    def duracion_estimada(self) -> float:
        return self.a.warmup + self.a.measure + self.a.cooldown + self.sobrecosto + self.a.descanso

    def dir_ronda(self, ronda: int) -> Path:
        return self.dir / f"ronda_{ronda:02d}"

    def preparar_ronda(self, ronda: int) -> list[dict]:
        d = self.dir_ronda(ronda)
        d.mkdir(parents=True, exist_ok=True)
        orden = orden_ronda(self.celdas, self.a.semilla, ronda)
        f = d / "orden.json"
        if f.exists():
            guardado = json.loads(f.read_text(encoding="utf-8"))
            if guardado["celdas"] != orden:
                raise Abortar(f"El orden guardado en {f} no coincide con la semilla/celdas actuales; "
                              "no se sobrescribe. Usa otra carpeta de resultados.")
        else:
            f.write_text(json.dumps({"ronda": ronda, "semilla": self.a.semilla, "celdas": orden},
                                    indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return orden

    def situacion(self, celda: dict, ronda: int, filas: set, descartes: dict) -> str:
        k = clave(celda, ronda)
        if k in filas:
            return "hecha"
        n = descartes.get(k, 0)
        return "agotada" if n >= 2 else ("reintento" if n == 1 else "pendiente")

    # ---------- revisión del entorno ----------
    def revisar(self, momento: str) -> None:
        info = self.sis.revisar_entorno(momento, self.a.min_free_gb)
        self.avisos_entorno = self.avisos_fijos + info.get("avisos", [])
        destino = self.dir / "revisiones_entorno"
        destino.mkdir(parents=True, exist_ok=True)
        marca = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        (destino / f"{marca}_{momento}.json").write_text(
            json.dumps(info, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        self.log(f"revisión de entorno ({momento})",
                 "OK" + (" | avisos: " + " | ".join(self.avisos_entorno) if self.avisos_entorno else ""))

    # ---------- interrupción previa ----------
    def recuperar_interrumpida(self) -> None:
        if not self.en_curso.exists():
            return
        d = json.loads(self.en_curso.read_text(encoding="utf-8"))
        rid = f"interrumpida_{d['inicio_epoch']}_{d['celda']['engine']}_{d['celda']['profile']}_" \
              f"{d['celda']['query_id']}_c{d['celda']['concurrency']}_r{d['ronda']}"
        self.sal.registrar_descarte(rid, d["celda"], d["ronda"], d["intento"],
                                    "corrida interrumpida a mitad (el orquestador se detuvo)")
        self.en_curso.unlink()
        self.log("corrida interrumpida registrada como descarte", rid)

    # ---------- entre corridas ----------
    def entre_corridas(self) -> bool:
        """Atiende DETENER y PAUSAR. Devuelve False si hay que detenerse."""
        if self.f_detener.exists():
            return False
        if self.f_pausar.exists():
            self.estado = "en pausa (borra el archivo PAUSAR para reanudar)"
            self.escribir_estado()
            self.log("pausa", f"archivo {self.f_pausar}", aviso=True)
            self.sis.impedir_suspension(False)
            while self.f_pausar.exists():
                if self.f_detener.exists():
                    return False
                self.dormir(ESPERA_S)
            self.log("reanudar", "se borró PAUSAR; revisando el entorno", aviso=True)
            self.sis.impedir_suspension(True)
            self.revisar("reanudar")
        if not self.sis.corriente():
            self.estado = "esperando corriente (D-012)"
            self.escribir_estado()
            self.log("esperando corriente", "la laptop está desconectada; no se inicia ninguna corrida",
                     aviso=True)
            while not self.sis.corriente():
                if self.f_detener.exists():
                    return False
                self.dormir(ESPERA_S)
            self.log("corriente restablecida", "revisando el entorno")
            self.revisar("reanudar")
        return True

    def cabe_antes_del_limite(self) -> bool:
        if not self.limite:
            return True
        return time.time() + self.duracion_estimada() <= self.limite.timestamp()

    # ---------- una corrida ----------
    def correr_celda(self, celda: dict, ronda: int, intento: int) -> str:
        """Devuelve 'fila', 'descarte' o lanza Abortar."""
        inicio = time.time()
        self.en_curso.write_text(json.dumps({"celda": celda, "ronda": ronda, "intento": intento,
                                             "inicio_epoch": int(inicio),
                                             "inicio_local": ahora_local()}) + "\n", encoding="utf-8")
        self.estado = f"corriendo {texto_celda(celda)} r{ronda} intento {intento}"
        self.escribir_estado()
        self.log("inicio corrida", f"{texto_celda(celda)} r{ronda} intento {intento}")
        self.sal.dir_runs = self.dir_ronda(ronda) / "runs"
        try:
            res = self.ejecutar(celda["engine"], celda["profile"], celda["query_id"],
                                celda["concurrency"], self.a.warmup, self.a.measure, self.a.cooldown,
                                self.a.timeout, permitir_mongodb_windows=False,
                                min_libre_gb=self.a.min_free_gb)
        except host.PrecondicionFallida as e:
            self.en_curso.unlink(missing_ok=True)
            if not self.sis.corriente():
                self.log("corrida no iniciada", f"corriente desconectada antes de medir: {e}")
                return "sin_iniciar"
            raise Abortar(f"precondición fallida antes de medir: {e}") from e
        except identidad.ServidorIncorrecto as e:
            self.en_curso.unlink(missing_ok=True)
            raise Abortar(str(e)) from e
        except Exception as e:  # noqa: BLE001
            rid = f"excepcion_{int(inicio)}_{celda['engine']}_{celda['profile']}_" \
                  f"{celda['query_id']}_c{celda['concurrency']}_r{ronda}"
            self.sal.registrar_descarte(rid, celda, ronda, intento, f"excepción del runner: {e!r}"[:500])
            self.en_curso.unlink(missing_ok=True)
            if not self.sis.docker_ok():
                raise Abortar(f"Docker dejó de responder durante la corrida ({e!r}).") from e
            return "descarte"
        r = self.sal.guardar_corrida(res, ronda, intento)
        self.en_curso.unlink(missing_ok=True)
        duracion = time.time() - inicio
        nominal = self.a.warmup + self.a.measure + self.a.cooldown
        self.sobrecosto = max(self.sobrecosto if self.corridas_sesion else 0.0, duracion - nominal,
                              30.0)
        self.corridas_sesion += 1
        self.respaldo_pendiente = True
        self.ultima = {"run_id": r["run_id"], "status": r["fila"]["status"],
                       "descartada": r["descartada"], "duracion_min": round(duracion / 60, 1),
                       "fin_local": ahora_local()}
        if r["descartada"]:
            self.log("descarte", f"{r['run_id']}: {r['descartada']}", aviso=True)
            return "descarte"
        if r["fila"]["status"] in ("oom", "error"):
            avisos.avisar("Benchmark: corrida con " + r["fila"]["status"], r["run_id"], "high")
        return "fila"

    # ---------- respaldo ----------
    def validar_respaldo(self) -> None:
        """Al arrancar: la ruta de --respaldo existe o se puede crear, no está dentro de
        results/ ni de evidence/, y se puede escribir. Si falla, no se arranca."""
        if not self.a.respaldo:
            self.avisos_fijos.append("Sin --respaldo: no se copiarán results/ ni evidence/.")
            return
        raiz = Path(self.a.respaldo).resolve()
        for protegido in ((config.RAIZ / "results").resolve(), (config.RAIZ / "evidence").resolve(),
                          self.dir):
            if raiz == protegido or protegido in raiz.parents:
                raise Abortar(f"--respaldo ({raiz}) no puede estar dentro de {protegido}.")
        try:
            raiz.mkdir(parents=True, exist_ok=True)
            prueba = raiz / f".prueba_escritura_{os.getpid()}"
            with prueba.open("w", encoding="utf-8") as f:
                f.write("prueba\n")
                f.flush()
                os.fsync(f.fileno())
            prueba.unlink()
        except OSError as e:
            raise Abortar(f"--respaldo ({raiz}) no se puede crear o escribir: {e}") from e
        self.log("respaldo validado", str(raiz))

    def respaldar(self, etiqueta: str) -> None:
        """Un fallo al respaldar no detiene las mediciones: se registra, se avisa y queda
        pendiente (se reintenta al terminar la ronda siguiente y al final)."""
        if not self.a.respaldo:
            self.log("respaldo omitido", f"{etiqueta}: no se indicó --respaldo")
            return
        try:
            self._respaldar(etiqueta)
        except Exception as e:  # noqa: BLE001
            self.respaldo_pendiente = True
            self.respaldos_fallidos.append({"etiqueta": etiqueta, "hora": ahora_local(), "error": repr(e)})
            self.log("respaldo fallido", f"{etiqueta}: {e!r}; queda pendiente y se reintentará",
                     aviso=True)

    def _respaldar(self, etiqueta: str) -> None:
        raiz_respaldo = Path(self.a.respaldo).resolve()
        marca = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destino = raiz_respaldo / f"{marca}_{self.dir.name}_{etiqueta}"
        if destino.exists():
            raise FileExistsError(f"El respaldo {destino} ya existe; no se sobrescribe.")
        evidencia = config.RAIZ / "evidence"
        shutil.copytree(self.dir, destino / self.dir.name)
        shutil.copytree(evidencia, destino / "evidence")
        lineas, distintos = [], []
        for f in sorted(p for p in destino.rglob("*") if p.is_file()):
            rel = f.relative_to(destino).as_posix()
            h = _sha256(f)
            origen = (self.dir / Path(rel).relative_to(self.dir.name)) if rel.startswith(self.dir.name + "/") \
                else evidencia / Path(rel).relative_to("evidence")
            if origen.exists() and _sha256(origen) != h:
                distintos.append(rel)
            lineas.append(f"{h}  {rel}\n")
        manifiesto = "".join(lineas)
        # En binario con LF: así `sha256sum -c MANIFIESTO.sha256` funciona también en Windows.
        (destino / "MANIFIESTO.sha256").write_bytes(manifiesto.encode("utf-8"))
        copia = self.dir / "manifiestos"
        copia.mkdir(exist_ok=True)
        (copia / f"{destino.name}.sha256").write_bytes(manifiesto.encode("utf-8"))
        self.respaldo_pendiente = False
        detalle = f"{destino} ({len(lineas)} archivos)" + \
                  (f"; DIFERENCIAS con el origen: {distintos}" if distintos else "; copia verificada")
        self.log("respaldo", detalle, aviso=bool(distintos))

    # ---------- estado.txt y resumen.md ----------
    def escribir_estado(self) -> None:
        filas, descartes = self.sal.claves_csv(), self.sal.descartes_por_clave()
        lineas = [f"Actualizado: {ahora_local()}", f"Estado: {self.estado}",
                  f"Carpeta de resultados: {self.dir}",
                  f"Hora límite: {self.limite.strftime('%Y-%m-%d %H:%M %Z') if self.limite else 'sin límite'}",
                  f"Duración estimada por corrida: {self.duracion_estimada() / 60:.1f} min"]
        total_hechas = 0
        for ronda in range(1, self.a.rondas + 1):
            sit = [self.situacion(c, ronda, filas, descartes) for c in self.celdas]
            hechas = sit.count("hecha")
            total_hechas += hechas
            lineas.append(f"Ronda {ronda}: {hechas}/{len(self.celdas)} con fila, "
                          f"{sit.count('reintento')} con reintento pendiente, {sit.count('agotada')} agotadas")
        if self.limite:
            restantes = self.a.rondas * len(self.celdas) - total_hechas
            caben = max(0, int((self.limite.timestamp() - time.time()) // self.duracion_estimada()))
            lineas.append(f"Corridas restantes: {restantes}; caben antes de la hora límite: ~{caben}")
        if self.ultima:
            lineas.append(f"Última corrida: {self.ultima['run_id']} status={self.ultima['status']} "
                          f"descartada={self.ultima['descartada']} duración={self.ultima['duracion_min']} min "
                          f"({self.ultima['fin_local']})")
        if self.siguiente:
            lineas.append(f"Siguiente: {self.siguiente}")
        for a in self.avisos_entorno:
            lineas.append(f"AVISO: {a}")
        if self.respaldo_pendiente and self.respaldos_fallidos:
            u = self.respaldos_fallidos[-1]
            lineas.append(f"AVISO: respaldo pendiente; último fallo {u['hora']} ({u['etiqueta']}): {u['error']}")
        lineas += ["", f"Pausar entre corridas:  crear el archivo {self.f_pausar}",
                   f"Reanudar:               borrar {self.f_pausar}",
                   f"Detener entre corridas: crear el archivo {self.f_detener}"]
        _escribir(self.dir / "estado.txt", "\n".join(lineas) + "\n")
        self.escribir_resumen(filas, descartes)

    def escribir_resumen(self, filas: set, descartes: dict) -> None:
        import csv as _csv
        por_ronda: dict[int, dict[str, int]] = {}
        especiales = []
        if self.sal.csv.exists():
            with self.sal.csv.open(encoding="utf-8", newline="") as f:
                for r in _csv.DictReader(f):
                    por_ronda.setdefault(int(r["run_number"]), {}).setdefault(r["status"], 0)
                    por_ronda[int(r["run_number"])][r["status"]] += 1
                    if r["status"] in ("spill", "oom", "error"):
                        especiales.append(r)
        md = [f"# Resumen del benchmark\n", f"Actualizado: {ahora_local()}  ",
              f"Estado: {self.estado}  ", f"Carpeta: `{self.dir}`\n",
              "| Ronda | Con fila | ok | spill | oom | error | Descartes | Agotadas |",
              "|---|---|---|---|---|---|---|---|"]
        for ronda in range(1, self.a.rondas + 1):
            c = por_ronda.get(ronda, {})
            desc = sum(v for k, v in descartes.items() if k[4] == ronda)
            agot = sum(1 for k, v in descartes.items() if k[4] == ronda and v >= 2 and k not in filas)
            md.append(f"| {ronda} | {sum(c.values())}/{len(self.celdas)} | {c.get('ok', 0)} | "
                      f"{c.get('spill', 0)} | {c.get('oom', 0)} | {c.get('error', 0)} | {desc} | {agot} |")
        md.append("\n## Corridas spill, oom y error\n")
        if especiales:
            md += ["| Ronda | Celda | status | P99 (ms) | Throttling (s) |", "|---|---|---|---|---|"]
            for r in especiales:
                md.append(f"| {r['run_number']} | {r['engine']} {r['profile']} {r['query_id']} "
                          f"c{r['concurrency']} | {r['status']} | {r['latency_p99_ms'] or '—'} | "
                          f"{r['cpu_throttled_seconds'] or '—'} |")
        else:
            md.append("Ninguna todavía.")
        res_dir, ev_dir = config.RAIZ / "results", config.RAIZ / "evidence"
        md += ["\n## Tamaños y espacio\n",
               f"- `results/`: {_tam(res_dir) / 2**20:.1f} MiB",
               f"- `evidence/`: {_tam(ev_dir) / 2**20:.1f} MiB",
               f"- Carpeta de esta ejecución: {_tam(self.dir) / 2**20:.1f} MiB",
               f"- Libre en C:: {shutil.disk_usage('C:/').free / 1e9:.1f} GB"]
        if self.avisos_entorno:
            md += ["\n## Avisos del entorno\n"] + [f"- {a}" for a in self.avisos_entorno]
        if self.respaldos_fallidos:
            md += ["\n## Respaldos fallidos\n"] + [
                f"- {r['hora']} {r['etiqueta']}: {r['error']}" for r in self.respaldos_fallidos]
            md.append(f"- Respaldo pendiente: {'sí' if self.respaldo_pendiente else 'no'}")
        _escribir(self.dir / "resumen.md", "\n".join(md) + "\n")

    # ---------- bucle principal ----------
    def correr(self) -> int:
        # Antes de escribir nada: el piloto (--celdas) nunca escribe en la carpeta de la matriz.
        if self.a.celdas and self.a.results_dir is None:
            print("ABORTADO: se indicó --celdas sin --results-dir; no se corre para no mezclar el "
                  "piloto con la matriz (usa, p. ej., --results-dir results/piloto).", file=sys.stderr)
            return 2
        if not host.tomar_candado():
            print("ABORTADO: ya hay otro orquestador corriendo (D-006: una corrida a la vez). "
                  "Espera a que termine o detenlo con DETENER.", file=sys.stderr)
            return 2
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log("arranque del orquestador",
                 f"rondas={self.a.rondas} celdas={len(self.celdas)} semilla={self.a.semilla} "
                 f"limite={self.limite} respaldo={self.a.respaldo} ntfy={'sí' if avisos.configurado() else 'no'}")
        try:
            self.validar_respaldo()
            self.recuperar_interrumpida()
            self.revisar("arranque")
            self.sis.impedir_suspension(True)
            motivo = self._bucle()
        except Abortar as e:
            self.estado = f"DETENIDO POR ERROR: {e}"
            self.escribir_estado()
            self.log("abortado", str(e), aviso=True)
            self.sis.impedir_suspension(False)
            return 2
        except KeyboardInterrupt:
            self.estado = "detenido con Ctrl+C (la corrida en curso se registrará como descarte al relanzar)"
            self.escribir_estado()
            self.log("detenido con Ctrl+C", "", aviso=True)
            self.sis.impedir_suspension(False)
            return 130
        if self.respaldo_pendiente:
            self.respaldar("parcial" if motivo != "terminado" else "final")
        self.estado = motivo
        self.siguiente = ""
        self.escribir_estado()
        self.log("fin del orquestador", motivo, aviso=True)
        self.sis.impedir_suspension(False)
        if motivo.startswith("detenido por DETENER"):
            self.f_detener.unlink(missing_ok=True)
        return 0

    def _bucle(self) -> str:
        for ronda in range(1, self.a.rondas + 1):
            orden = self.preparar_ronda(ronda)
            for celda in orden:
                while True:
                    sit = self.situacion(celda, ronda, self.sal.claves_csv(), self.sal.descartes_por_clave())
                    if sit in ("hecha", "agotada"):
                        break
                    intento = 2 if sit == "reintento" else 1
                    self.siguiente = f"{texto_celda(celda)} r{ronda} intento {intento}"
                    if not self.entre_corridas():
                        return "detenido por DETENER (entre corridas)"
                    if not self.cabe_antes_del_limite():
                        self.log("hora límite", f"no cabe {self.siguiente} "
                                 f"(~{self.duracion_estimada() / 60:.1f} min) antes de {self.limite}",
                                 aviso=True)
                        return "detenido por hora límite"
                    if self.a.descanso:
                        self.dormir(self.a.descanso)
                    resultado = self.correr_celda(celda, ronda, intento)
                    self.estado = "entre corridas"
                    self.escribir_estado()
                    if resultado == "fila":
                        break
            completas = all(self.situacion(c, ronda, self.sal.claves_csv(), self.sal.descartes_por_clave())
                            in ("hecha", "agotada") for c in orden)
            if completas and not (self.dir_ronda(ronda) / "RONDA_TERMINADA").exists():
                (self.dir_ronda(ronda) / "RONDA_TERMINADA").write_text(ahora_local() + "\n", encoding="utf-8")
                self.log("ronda terminada", f"ronda {ronda}", aviso=True)
                self.respaldar(f"ronda_{ronda:02d}")
        return "terminado"


def _sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def _tam(ruta: Path) -> int:
    return sum(p.stat().st_size for p in ruta.rglob("*") if p.is_file()) if ruta.exists() else 0


def _escribir(ruta: Path, texto: str) -> None:
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(texto)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ruta)


def _hora_limite(texto: str) -> datetime:
    """'2026-11-01 20:00' en hora de Ciudad de México."""
    return datetime.strptime(texto, "%Y-%m-%d %H:%M").replace(tzinfo=ZONA)


def construir_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Orquestador de la matriz del benchmark")
    ap.add_argument("--rondas", type=int, default=3)
    ap.add_argument("--semilla", type=int, default=SEMILLA_ORDEN)
    ap.add_argument("--hora-limite", type=_hora_limite, default=None,
                    help="'AAAA-MM-DD HH:MM' (hora de Ciudad de México)")
    ap.add_argument("--results-dir", type=Path, default=None,
                    help="por defecto results/; obligatorio si se usa --celdas (piloto)")
    ap.add_argument("--respaldo", type=Path, default=None, help="segunda ubicación para copias")
    ap.add_argument("--celdas", type=parsear_celda, nargs="+", default=None,
                    help="subconjunto, p. ej. postgres:C:Q1:50 (para el piloto)")
    ap.add_argument("--warmup", type=float, default=60)
    ap.add_argument("--measure", type=float, default=300)
    ap.add_argument("--cooldown", type=float, default=60)
    ap.add_argument("--timeout", type=float, default=run_cell.TIMEOUT_S)
    ap.add_argument("--descanso", type=float, default=0, help="segundos de espera entre corridas")
    ap.add_argument("--min-free-gb", type=float, default=host.MIN_LIBRE_GB)
    ap.add_argument("--dry-run", action="store_true", help="muestra el orden y el estado; no mide")
    ap.add_argument("--revisar-entorno", action="store_true",
                    help="solo la revisión del entorno y del respaldo; no mide ni escribe resultados")
    return ap


def dry_run(args) -> int:
    o = Orquestador(args)
    filas, desc = o.sal.claves_csv(), o.sal.descartes_por_clave()
    for ronda in range(1, args.rondas + 1):
        orden = orden_ronda(o.celdas, args.semilla, ronda)
        print(f"Ronda {ronda} ({len(orden)} celdas):")
        for i, c in enumerate(orden, 1):
            print(f"  {i:2d}. {texto_celda(c):<38} {o.situacion(c, ronda, filas, desc)}")
    print(f"Duración estimada por corrida: {o.duracion_estimada() / 60:.1f} min; "
          f"total: {o.duracion_estimada() * args.rondas * len(o.celdas) / 3600:.1f} h")
    return 0


def revisar_entorno(args) -> int:
    """Revisión del entorno (D-009) y validación del respaldo, sin medir ni escribir resultados.
    Recrea brevemente cada motor con el Perfil C para leer el cgroup y contar documentos."""
    if not host.tomar_candado():
        print("ERROR: hay un orquestador corriendo; la revisión recrearía sus contenedores.")
        return 2
    o = Orquestador(args)
    o.sin_escritura = True
    try:
        o.validar_respaldo()
        info = o.sis.revisar_entorno("revision_manual", args.min_free_gb)
    except (Abortar, host.PrecondicionFallida, identidad.ServidorIncorrecto) as e:
        print(f"ERROR: {e}")
        return 2
    print(json.dumps({"powercfg_ac": info["suspension"]["valores"], "plan": info["plan_energia"],
                      "modo_energia": info["modo_energia"],
                      **{m: {"documentos": info[m]["documentos"], "limites_C": info[m]["identidad"]["limites"],
                             "version": info[m]["identidad"]["version"],
                             "precondiciones": info[m]["precondiciones"]} for m in MOTORES}},
                     indent=2, ensure_ascii=False))
    avisos_ = o.avisos_fijos + info["avisos"]
    print("AVISOS:" if avisos_ else "Sin avisos.")
    for a in avisos_:
        print(f"  - {a}")
    return 0


def main() -> int:
    args = construir_parser().parse_args()
    if args.celdas and args.results_dir is None and not args.dry_run:
        print("ABORTADO: se indicó --celdas sin --results-dir; no se corre para no mezclar el "
              "piloto con la matriz (usa, p. ej., --results-dir results/piloto).", file=sys.stderr)
        return 2
    if args.dry_run:
        return dry_run(args)
    if args.revisar_entorno:
        return revisar_entorno(args)
    return Orquestador(args).correr()


if __name__ == "__main__":
    sys.exit(main())
