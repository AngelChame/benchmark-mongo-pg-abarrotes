#!/usr/bin/env python3
"""Prueba automática de pausar, reanudar, detener y retomar del orquestador (D-009).

Secuencia (tiempos cortos, una celda en 3 rondas; NO es una medición):
  1. Lanzar el orquestador.
  2. Durante la ronda 1, crear PAUSAR.
  3. Esperar "en pausa"; comprobar que en pausa no se inicia ninguna corrida.
  4. Borrar PAUSAR y comprobar que reanuda (con revisión de entorno).
  5. Durante la ronda 2, crear DETENER y esperar a que termine solo.
  6. Relanzar el mismo comando y comprobar que solo corre la ronda 3.
  7. Comprobar CSV (3 filas, sin duplicados), descartes, bitácora y respaldos con
     manifiesto SHA-256.
Cada paso queda con hora en <results-dir>/prueba_pausa.log. Resultado: PASA o FALLA.

Uso:
    .venv/Scripts/python runner/prueba_pausa.py --results-dir results/piloto_pausa \\
        --respaldo C:\\benchmark-respaldo
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

RAIZ = Path(__file__).resolve().parent.parent
ZONA = ZoneInfo("America/Mexico_City")
CELDA = "mongo:A:Q2:1"


class Falla(RuntimeError):
    pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", type=Path, required=True)
    ap.add_argument("--respaldo", type=Path, required=True)
    a = ap.parse_args()
    d = (RAIZ / a.results_dir).resolve() if not a.results_dir.is_absolute() else a.results_dir
    if (d / "benchmark_results.csv").exists():
        print(f"FALLA: {d} ya tiene resultados; la prueba necesita una carpeta nueva.")
        return 1
    d.mkdir(parents=True, exist_ok=True)
    registro = d / "prueba_pausa.log"
    estado, bitacora, csv_ = d / "estado.txt", d / "bitacora.log", d / "benchmark_results.csv"
    pausar, detener = d / "PAUSAR", d / "DETENER"
    respaldos_previos = set(os.listdir(a.respaldo)) if a.respaldo.exists() else set()

    def log(msg: str) -> None:
        linea = f"{datetime.now(ZONA).strftime('%Y-%m-%d %H:%M:%S %Z')} | {msg}"
        print(linea, flush=True)
        with registro.open("a", encoding="utf-8") as f:
            f.write(linea + "\n")

    def leer(p: Path) -> str:
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            return ""

    def filas() -> list[dict]:
        if not csv_.exists():
            return []
        with csv_.open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))

    def esperar(desc: str, cond, limite_s: float, proc=None) -> None:
        t0 = time.time()
        while time.time() - t0 < limite_s:
            if cond():
                log(f"OK: {desc} ({time.time() - t0:.0f} s)")
                return
            if proc is not None and proc.poll() is not None and not cond():
                raise Falla(f"el orquestador terminó (código {proc.returncode}) antes de: {desc}")
            time.sleep(2)
        raise Falla(f"no ocurrió en {limite_s:.0f} s: {desc}")

    cmd = [sys.executable, str(RAIZ / "runner" / "orquestador.py"), "--results-dir", str(d),
           "--rondas", "3", "--celdas", CELDA, "--warmup", "5", "--measure", "15",
           "--cooldown", "5", "--respaldo", str(a.respaldo)]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")

    def lanzar(n: int):
        salida = (d / f"prueba_pausa_lanzamiento{n}.out").open("w", encoding="utf-8")
        log(f"lanzamiento {n}: {' '.join(cmd)}")
        return subprocess.Popen(cmd, stdout=salida, stderr=subprocess.STDOUT, env=env, cwd=RAIZ)

    try:
        log("== INICIO de la prueba de pausa")
        p = lanzar(1)
        esperar("ronda 1 corriendo", lambda: "Estado: corriendo" in leer(estado)
                and " r1 " in leer(estado), 300, p)
        pausar.write_text("", encoding="utf-8")
        log("PAUSAR creado durante la ronda 1")
        esperar("estado 'en pausa'", lambda: "Estado: en pausa" in leer(estado), 600, p)
        if len(filas()) != 1:
            raise Falla(f"al entrar en pausa debería haber 1 fila; hay {len(filas())}")
        log("OK: 1 fila en el CSV al entrar en pausa")
        inicios = leer(bitacora).count("| inicio corrida |")
        time.sleep(45)
        if leer(bitacora).count("| inicio corrida |") != inicios or "en pausa" not in leer(estado):
            raise Falla("se inició una corrida durante la pausa")
        log("OK: 45 s en pausa sin iniciar corridas")
        pausar.unlink()
        log("PAUSAR borrado")
        esperar("bitácora 'reanudar'", lambda: "| reanudar |" in leer(bitacora), 120, p)
        esperar("revisión de entorno al reanudar",
                lambda: "revisión de entorno (reanudar)" in leer(bitacora), 300, p)
        esperar("ronda 2 corriendo", lambda: "Estado: corriendo" in leer(estado)
                and " r2 " in leer(estado), 300, p)
        detener.write_text("", encoding="utf-8")
        log("DETENER creado durante la ronda 2")
        esperar("el orquestador termina solo", lambda: p.poll() is not None, 600)
        if p.returncode != 0:
            raise Falla(f"el orquestador terminó con código {p.returncode}")
        if "detenido por DETENER" not in leer(bitacora):
            raise Falla("la bitácora no registra 'detenido por DETENER'")
        if detener.exists():
            raise Falla("DETENER no se borró al detenerse")
        if [f["run_number"] for f in filas()] != ["1", "2"]:
            raise Falla(f"tras DETENER se esperaban las rondas 1 y 2; hay {[f['run_number'] for f in filas()]}")
        log("OK: detenido con las rondas 1 y 2 terminadas; DETENER borrado")

        inicios_antes = leer(bitacora).count("| inicio corrida |")
        p2 = lanzar(2)
        esperar("el relanzamiento termina", lambda: p2.poll() is not None, 900)
        if p2.returncode != 0:
            raise Falla(f"el relanzamiento terminó con código {p2.returncode}")
        nuevas = leer(bitacora).count("| inicio corrida |") - inicios_antes
        rondas = [f["run_number"] for f in filas()]
        if nuevas != 1 or rondas != ["1", "2", "3"]:
            raise Falla(f"el relanzamiento debía correr solo la ronda 3; corridas nuevas={nuevas}, filas={rondas}")
        log("OK: el relanzamiento corrió solo la ronda 3; CSV con rondas 1, 2 y 3 sin duplicados")
        if (d / "descartes.csv").exists():
            raise Falla("hay descartes inesperados: " + leer(d / "descartes.csv"))
        log("OK: sin descartes")

        nuevos = sorted(set(os.listdir(a.respaldo)) - respaldos_previos)
        etiquetas = {n.rsplit("_", 2)[-2] + "_" + n.rsplit("_", 1)[-1] for n in nuevos}
        if not {"ronda_01", "ronda_02", "ronda_03"} <= etiquetas:
            raise Falla(f"faltan respaldos por ronda; hay {nuevos}")
        for n in nuevos:
            base = a.respaldo / n
            for linea in (base / "MANIFIESTO.sha256").read_text(encoding="utf-8").splitlines():
                h, rel = linea.split("  ", 1)
                if hashlib.sha256((base / rel).read_bytes()).hexdigest() != h:
                    raise Falla(f"el manifiesto no coincide: {n}/{rel}")
        log(f"OK: respaldos {nuevos} con manifiesto SHA-256 verificado")
        log("== RESULTADO: PASA")
        return 0
    except Falla as e:
        log(f"== RESULTADO: FALLA — {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
