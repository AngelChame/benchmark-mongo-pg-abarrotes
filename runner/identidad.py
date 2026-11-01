"""Verifica que el servidor al que se conecta el runner es el contenedor esperado.

Regla del proyecto: todo se conecta a los puertos de los contenedores
(Mongo 27018, PostgreSQL 5432) y antes de cada corrida se comprueban
  1) la versión exacta que reporta el servidor, y
  2) un dato propio del contenedor:
     - Mongo: hostname del servidor = Config.Hostname de `bench-mongo`;
     - PostgreSQL: inet_server_addr() = IP del contenedor `bench-postgres`.
Si algo no coincide, se aborta con un error claro. El MongoDB de Windows
(puerto 27017) reportaría otra versión y el nombre de la laptop.
"""

from __future__ import annotations

import json
import subprocess

from runner import config


class ServidorIncorrecto(RuntimeError):
    """El servidor no es el contenedor esperado: no se debe medir."""


def _abortar(motor: str, detalle: str) -> None:
    raise ServidorIncorrecto(
        f"ABORTADO [{motor}]: el servidor no es el contenedor esperado "
        f"({config.CONTENEDOR[motor]}). {detalle}")


def _inspect(contenedor: str) -> dict:
    r = subprocess.run(["docker", "inspect", contenedor], capture_output=True, text=True)
    if r.returncode != 0:
        raise ServidorIncorrecto(f"ABORTADO: no existe el contenedor {contenedor} "
                                 f"(¿está levantado?). {r.stderr.strip()}")
    info = json.loads(r.stdout)[0]
    if not info["State"]["Running"]:
        raise ServidorIncorrecto(f"ABORTADO: el contenedor {contenedor} no está corriendo.")
    return info


def _version_ok(motor: str, version: str) -> bool:
    esperada = config.VERSION_ESPERADA[motor]
    return version == esperada or version.startswith(esperada + " ")


def verificar_mongo(cliente) -> dict:
    """Comprueba un pymongo.MongoClient. Devuelve los datos de identidad."""
    cliente.admin.command("ping")
    host, puerto = cliente.address
    if puerto in config.PUERTOS_PROHIBIDOS or puerto != config.MONGO_PORT:
        _abortar("mongo", f"Conectado a {host}:{puerto}; se esperaba el puerto {config.MONGO_PORT}.")
    info = _inspect(config.CONTENEDOR["mongo"])
    version = cliente.admin.command("buildInfo")["version"]
    hostname_srv = cliente.admin.command("hostInfo")["system"]["hostname"].split(":")[0]
    hostname_cont = info["Config"]["Hostname"]
    if not _version_ok("mongo", version):
        _abortar("mongo", f"Versión {version!r}; se esperaba {config.VERSION_ESPERADA['mongo']!r}.")
    if hostname_srv != hostname_cont:
        _abortar("mongo", f"El servidor se llama {hostname_srv!r}; el contenedor, {hostname_cont!r}.")
    return {"motor": "mongo", "version": version, "puerto": puerto,
            "contenedor_id": info["Id"][:12], "hostname": hostname_srv,
            "imagen": info["Config"]["Image"]}


def verificar_postgres(conn) -> dict:
    """Comprueba una conexión psycopg. Devuelve los datos de identidad."""
    puerto = conn.info.port
    if puerto != config.PG_PORT:
        _abortar("postgres", f"Conectado al puerto {puerto}; se esperaba {config.PG_PORT}.")
    info = _inspect(config.CONTENEDOR["postgres"])
    with conn.cursor() as cur:
        cur.execute("SELECT current_setting('server_version'), host(inet_server_addr())")
        version, ip_srv = cur.fetchone()
    ips_cont = {r["IPAddress"] for r in info["NetworkSettings"]["Networks"].values()
                if r.get("IPAddress")}
    if not _version_ok("postgres", version):
        _abortar("postgres", f"Versión {version!r}; se esperaba {config.VERSION_ESPERADA['postgres']!r}.")
    if ip_srv not in ips_cont:
        _abortar("postgres", f"El servidor atiende en {ip_srv!r}; el contenedor tiene {sorted(ips_cont)}.")
    return {"motor": "postgres", "version": version, "puerto": puerto,
            "contenedor_id": info["Id"][:12], "ip": ip_srv, "imagen": info["Config"]["Image"]}


def verificar_limites(motor: str, perfil: str) -> dict:
    """Lee el cgroup dentro del contenedor y lo compara con el perfil."""
    p = config.leer_perfil(perfil)
    esperado_mem = int(p["MEM_LIMIT"].rstrip("m")) * 1024 * 1024
    esperado_cpu = f"{int(float(p['CPUS']) * 100000)} 100000"
    r = subprocess.run(["docker", "exec", config.CONTENEDOR[motor], "sh", "-c",
                        "cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/memory.swap.max "
                        "/sys/fs/cgroup/cpu.max"], capture_output=True, text=True)
    if r.returncode != 0:
        raise ServidorIncorrecto(f"ABORTADO [{motor}]: no se pudo leer el cgroup. {r.stderr.strip()}")
    mem, swap, cpu = r.stdout.strip().splitlines()
    real = {"memory_max": int(mem), "memory_swap_max": swap, "cpu_max": cpu}
    if real["memory_max"] != esperado_mem or swap != "0" or cpu != esperado_cpu:
        raise ServidorIncorrecto(
            f"ABORTADO [{motor}]: límites del cgroup distintos del perfil {perfil}: "
            f"real={real}, esperado memory_max={esperado_mem}, swap=0, cpu_max={esperado_cpu!r}.")
    return real


def conectar_mongo():
    """Conecta al contenedor de Mongo (27018) y verifica su identidad."""
    from pymongo import MongoClient
    cliente = MongoClient(config.MONGO_URI)
    return cliente, verificar_mongo(cliente)


def conectar_postgres(autocommit: bool = True):
    """Conecta al contenedor de PostgreSQL (5432) y verifica su identidad."""
    import psycopg
    conn = psycopg.connect(config.PG_DSN, autocommit=autocommit)
    return conn, verificar_postgres(conn)
