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


# --------------------------------------------------------------------------
# Suspensión del equipo (D-009): powercfg y bloqueo mientras corre la matriz
# --------------------------------------------------------------------------
_POWERCFG = {
    "suspender_tras_s": ("SUB_SLEEP", "STANDBYIDLE"),
    "hibernar_tras_s": ("SUB_SLEEP", "HIBERNATEIDLE"),
    "accion_tapa": ("SUB_BUTTONS", "LIDACTION"),   # 0 = no hacer nada
}


def _indice_ac(subgrupo: str, ajuste: str) -> int | None:
    """Valor en corriente alterna de un ajuste de powercfg (None si no se puede leer)."""
    import re
    try:
        r = subprocess.run(["powercfg", "/query", "SCHEME_CURRENT", subgrupo, ajuste],
                           capture_output=True, text=True, timeout=30,
                           encoding="oem", errors="replace")
    except Exception:  # noqa: BLE001
        return None
    valores = re.findall(r":\s*(0x[0-9a-fA-F]{8})\s*$", r.stdout, flags=re.M)
    # Las dos últimas son el índice actual en corriente alterna y en batería, en ese orden.
    return int(valores[-2], 16) if len(valores) >= 2 else None


def revisar_suspension() -> dict:
    """Lee con powercfg si el equipo conectado puede suspenderse.

    Devuelve {'valores', 'problemas', 'no_comprobado'}. Un problema impide arrancar;
    lo no comprobable se avisa.
    """
    valores = {k: _indice_ac(*v) for k, v in _POWERCFG.items()}
    problemas, no_comprobado = [], []
    for clave, valor in valores.items():
        if valor is None:
            no_comprobado.append(clave)
        elif valor != 0:
            problemas.append(f"{clave} = {valor} (debe ser 0 con la laptop conectada)")
    return {"valores": valores, "problemas": problemas, "no_comprobado": no_comprobado,
            "comandos_correccion": ["powercfg /change standby-timeout-ac 0",
                                    "powercfg /change hibernate-timeout-ac 0",
                                    "powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0",
                                    "powercfg /setactive SCHEME_CURRENT"]}


def plan_energia() -> str:
    r = subprocess.run(["powercfg", "/getactivescheme"], capture_output=True, text=True,
                       encoding="oem", errors="replace")
    return r.stdout.strip()


def impedir_suspension(activar: bool = True) -> bool:
    """Pide a Windows que no suspenda mientras corre el proceso (no cubre cerrar la tapa)."""
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if activar else 0)
        return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
    except Exception:  # noqa: BLE001
        return False
