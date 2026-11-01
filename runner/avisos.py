"""Avisos opcionales al celular con ntfy (https://ntfy.sh).

Lee NTFY_TOPIC (y, opcionalmente, NTFY_SERVER) del archivo .env en la raíz del
repositorio (.env no se versiona). Si no hay tema, no hace nada. Un fallo al
enviar nunca detiene la matriz.

    # .env
    NTFY_TOPIC=mi-tema-secreto
    NTFY_SERVER=https://ntfy.sh
"""

from __future__ import annotations

import urllib.parse
import urllib.request

from runner import config


def _leer_env() -> dict:
    ruta = config.RAIZ / ".env"
    datos = {}
    if ruta.exists():
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            if "=" in linea and not linea.lstrip().startswith("#"):
                k, v = linea.split("=", 1)
                datos[k.strip()] = v.strip().strip('"').strip("'")
    return datos


def configurado() -> bool:
    return bool(_leer_env().get("NTFY_TOPIC"))


def avisar(titulo: str, mensaje: str, prioridad: str = "default") -> bool:
    """Envía un aviso. Devuelve True si se envió; False si no hay tema o falló."""
    env = _leer_env()
    tema = env.get("NTFY_TOPIC")
    if not tema:
        return False
    servidor = env.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    # Título y prioridad como parámetros de la URL (admite acentos, a diferencia de los encabezados).
    consulta = urllib.parse.urlencode({"title": titulo, "priority": prioridad})
    req = urllib.request.Request(f"{servidor}/{urllib.parse.quote(tema)}?{consulta}",
                                 data=mensaje.encode("utf-8"), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10):
            return True
    except Exception:  # noqa: BLE001
        return False
