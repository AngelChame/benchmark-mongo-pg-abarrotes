"""Configuración común del benchmark (puertos, contenedores, versiones, variantes)."""

from __future__ import annotations

from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Conexión desde Windows a los puertos publicados por los contenedores.
# El 27017 de Windows es un MongoDB local ajeno al experimento: nunca se usa.
MONGO_HOST, MONGO_PORT = "127.0.0.1", 27018
PG_HOST, PG_PORT = "127.0.0.1", 5432
PUERTOS_PROHIBIDOS = {27017}
MONGO_URI = (f"mongodb://{MONGO_HOST}:{MONGO_PORT}/?directConnection=true"
             "&serverSelectionTimeoutMS=5000")
PG_DSN = (f"host={PG_HOST} port={PG_PORT} dbname=benchmark user=bench "
          "password=bench connect_timeout=5 application_name=benchmark")
BASE = "benchmark"
COLECCION = "ventas"

CONTENEDOR = {"mongo": "bench-mongo", "postgres": "bench-postgres"}

# Deben coincidir con las etiquetas fijadas en docker-compose.yml.
VERSION_ESPERADA = {"mongo": "9.0.4", "postgres": "18.6"}

PERFILES = ("PROFILE_A_BASELINE", "PROFILE_B_MEDIUM", "PROFILE_C_SEVERE")


def leer_perfil(perfil: str) -> dict:
    """Lee profiles/<perfil>.env y devuelve {'PROFILE', 'CPUS', 'MEM_LIMIT'}."""
    datos = {}
    for linea in (RAIZ / "profiles" / f"{perfil}.env").read_text(encoding="utf-8").splitlines():
        if "=" in linea and not linea.lstrip().startswith("#"):
            k, v = linea.split("=", 1)
            datos[k.strip()] = v.strip()
    return datos


# Variantes de memoria (D-008). Q1 usa la configuración por defecto de cada motor.
VARIANTES = {
    "Q1": {"mongo": {}, "postgres": {}},
    "Q2": {"mongo": {"allowDiskUse": False}, "postgres": {"work_mem": "4MB"}},
    "Q2_RELAXED": {"mongo": {"allowDiskUse": True}, "postgres": {"work_mem": "64MB"}},
}
