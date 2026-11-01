"""Comprobaciones del equipo antes y durante cada corrida (D-009, D-012, D-020, D-022)."""

from __future__ import annotations

import shutil
import subprocess

from runner import config

# Espacio libre mínimo antes de cada corrida. Q1 en PostgreSQL escribe ~0,85 GB de
# temporales por consulta; con 50 clientes simultáneos pueden ser ~45 GB a la vez.
MIN_LIBRE_GB = 60.0
UNIDAD_WINDOWS = "C:/"          # el disco virtual de Docker Desktop vive en C:
IMAGEN_FLUSH = ("postgres:18.6@sha256:"
                "3a4264520e121176619f54ff867d12774bc81e95e7b84db58d63bccdb6144b81")
RUTA_DATOS = {"mongo": "/data/db", "postgres": "/var/lib/postgresql"}


class PrecondicionFallida(RuntimeError):
    """No se cumple una condición del equipo: no se debe medir."""


def _powershell(comando: str) -> str:
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", comando],
                       capture_output=True, text=True, timeout=30)
    return r.stdout.strip()


def corriente_conectada() -> bool:
    """True si la laptop está conectada a la corriente (D-012)."""
    estado = _powershell("Add-Type -AssemblyName System.Windows.Forms; "
                         "[System.Windows.Forms.SystemInformation]::PowerStatus.PowerLineStatus")
    return estado == "Online"


def estado_mongodb_windows() -> str:
    """Estado del servicio de Windows `MongoDB` (D-020): Running, Stopped o NoExiste."""
    estado = _powershell("$s = Get-Service MongoDB -ErrorAction SilentlyContinue; "
                         "if ($s) { $s.Status } else { 'NoExiste' }")
    return estado or "Desconocido"


def espacio_libre(motor: str) -> dict:
    """GB libres en C: (Windows) y en el disco de datos de la VM (dentro del contenedor)."""
    libre_c = shutil.disk_usage(UNIDAD_WINDOWS).free / 1e9
    r = subprocess.run(["docker", "exec", config.CONTENEDOR[motor], "df", "-B1",
                        "--output=avail", RUTA_DATOS[motor]],
                       capture_output=True, text=True)
    libre_vm = int(r.stdout.split()[-1]) / 1e9 if r.returncode == 0 else None
    efectivo = min(x for x in (libre_c, libre_vm) if x is not None)
    return {"libre_windows_gb": round(libre_c, 1),
            "libre_vm_gb": round(libre_vm, 1) if libre_vm is not None else None,
            "libre_efectivo_gb": round(efectivo, 1)}


def drop_caches() -> None:
    """sync; echo 3 > /proc/sys/vm/drop_caches en la VM de Docker (DOC1 §5.5)."""
    r = subprocess.run(["docker", "run", "--rm", "--privileged", "--entrypoint", "sh",
                        IMAGEN_FLUSH, "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise PrecondicionFallida(f"drop_caches falló: {r.stderr.strip()}")


def verificar_precondiciones(motor: str, permitir_mongodb_windows: bool = False,
                             min_libre_gb: float = MIN_LIBRE_GB) -> dict:
    """Corriente, servicio MongoDB detenido y espacio libre. Lanza PrecondicionFallida."""
    info = {"corriente": corriente_conectada(), "mongodb_windows": estado_mongodb_windows(),
            **espacio_libre(motor), "min_libre_gb": min_libre_gb}
    if not info["corriente"]:
        raise PrecondicionFallida("La laptop no está conectada a la corriente (D-012).")
    if info["mongodb_windows"] == "Running" and not permitir_mongodb_windows:
        raise PrecondicionFallida(
            "El servicio de Windows 'MongoDB' está encendido (D-020). Detenlo con "
            "'Stop-Service MongoDB' en PowerShell como administrador.")
    if info["libre_efectivo_gb"] < min_libre_gb:
        raise PrecondicionFallida(
            f"Espacio libre insuficiente: {info['libre_efectivo_gb']} GB < {min_libre_gb} GB.")
    return info
