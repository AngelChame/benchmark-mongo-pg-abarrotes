"""Control de los contenedores: un solo motor encendido a la vez (D-006)."""

from __future__ import annotations

import subprocess

from runner import config, identidad


def compose(*args: str, perfil: str | None = None) -> str:
    cmd = ["docker", "compose"]
    if perfil:
        cmd += ["--env-file", f"profiles/{perfil}.env"]
    r = subprocess.run(cmd + list(args), capture_output=True, text=True,
                       encoding="utf-8", cwd=config.RAIZ)
    if r.returncode != 0:
        raise RuntimeError(f"docker compose {' '.join(args)} falló:\n{r.stdout}\n{r.stderr}")
    return r.stdout


def apagar(motor: str) -> None:
    compose("--profile", motor, "stop", motor)


def encender_solo(motor: str, perfil: str, recrear: bool = False) -> dict:
    """Apaga el otro motor, levanta `motor` con los límites de `perfil` y verifica
    identidad (versión y dato propio del contenedor) y límites del cgroup."""
    otro = "postgres" if motor == "mongo" else "mongo"
    apagar(otro)
    extra = ["--force-recreate"] if recrear else []
    compose("--profile", motor, "up", "-d", "--wait", *extra, motor, perfil=perfil)
    if motor == "mongo":
        cliente, info = identidad.conectar_mongo()
        cliente.close()
    else:
        conn, info = identidad.conectar_postgres()
        conn.close()
    info["limites"] = identidad.verificar_limites(motor, perfil)
    info["perfil"] = perfil
    return info
