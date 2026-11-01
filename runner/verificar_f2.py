#!/usr/bin/env python3
"""F2: verificación formal antes del piloto, guardada como evidencia.

evidence/entorno/       especificaciones del host, Windows, WSL, Docker, kernel,
                        versiones y digests (sección 1 del reporte)
evidence/verificacion/  condiciones de D-005 con D-016, límites reales de A/B/C en
                        ambos motores y prueba de drop_caches

Los planes explain de Q2 ya están en evidence/plans/ y no se repiten aquí.

Uso:
    .venv/Scripts/python runner/verificar_f2.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import platform
import random
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runner import check_queries, config, contenedores, host, identidad  # noqa: E402

EV = config.RAIZ / "evidence"
D_ENT, D_VER = EV / "entorno", EV / "verificacion"
DATASET = config.RAIZ / "data" / "ventas.ndjson"
IMAGENES = ["mongo:9.0.4", "postgres:18.6", "prom/prometheus:v3.15.1",
            "prom/node-exporter:v1.12.1", "ghcr.io/google/cadvisor:v0.60.6"]
MIN_KB = 0.85


def ahora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sh(cmd: list[str], utf16: bool = False) -> str:
    r = subprocess.run(cmd, capture_output=True)
    datos = r.stdout + r.stderr
    if utf16 or b"\x00" in datos[:200]:
        return datos.decode("utf-16-le", errors="replace").replace("\x00", "").strip()
    for cod in ("utf-8", "oem"):
        try:
            return datos.decode(cod).strip()
        except (UnicodeDecodeError, LookupError):
            continue
    return datos.decode("latin-1").strip()


def ps_json(script: str):
    return json.loads(sh(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                          script + " | ConvertTo-Json -Depth 4"]))


def guardar(ruta: Path, datos) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(datos, str):
        ruta.write_text(datos.rstrip() + "\n", encoding="utf-8")
    else:
        ruta.write_text(json.dumps(datos, indent=2, ensure_ascii=False, default=str) + "\n",
                        encoding="utf-8")


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# --------------------------------------------------------------------------
# Entorno
# --------------------------------------------------------------------------
def entorno() -> dict:
    crudo = D_ENT / "crudo"
    host_info = ps_json(
        "$os=Get-CimInstance Win32_OperatingSystem; $cpu=Get-CimInstance Win32_Processor; "
        "$cs=Get-CimInstance Win32_ComputerSystem; "
        "[pscustomobject]@{ so=$os.Caption; version=$os.Version; build=$os.BuildNumber; "
        "arquitectura=$os.OSArchitecture; cpu=$cpu.Name.Trim(); nucleos=$cpu.NumberOfCores; "
        "hilos=$cpu.NumberOfLogicalProcessors; mhz_max=$cpu.MaxClockSpeed; "
        "ram_bytes=$cs.TotalPhysicalMemory; fabricante=$cs.Manufacturer; modelo=$cs.Model; "
        "discos=@(Get-CimInstance Win32_DiskDrive | ForEach-Object { \"$($_.Model) ($($_.Size) bytes)\" }); "
        "bateria=[bool](Get-CimInstance Win32_Battery) }")
    wsl_version = sh(["wsl", "--version"], utf16=True)
    wsl_lista = sh(["wsl", "-l", "-v"], utf16=True)
    wslconfig = Path.home() / ".wslconfig"
    docker_version = json.loads(sh(["docker", "version", "--format", "{{json .}}"]))
    docker_info = json.loads(sh(["docker", "info", "--format", "{{json .}}"]))
    info_sel = {k: docker_info.get(k) for k in (
        "ServerVersion", "OperatingSystem", "OSType", "KernelVersion", "Architecture", "NCPU",
        "MemTotal", "CgroupVersion", "CgroupDriver", "Driver", "DockerRootDir", "SwapLimit",
        "MemoryLimit", "CpuCfsPeriod", "CpuCfsQuota")}
    compose = sh(["docker", "compose", "version", "--short"])
    imagenes = {}
    for img in IMAGENES:
        imagenes[img] = json.loads(sh(["docker", "image", "inspect", "--format",
                                       "{{json .RepoDigests}}", img]))
    meminfo_vm = sh(["docker", "run", "--rm", "--entrypoint", "sh", host.IMAGEN_FLUSH, "-c",
                     "head -3 /proc/meminfo; grep -E '^Swap(Total|Free)' /proc/meminfo; "
                     "nproc; uname -a"])
    pip = sh([sys.executable, "-m", "pip", "freeze"])
    commit = sh(["git", "-C", str(config.RAIZ), "rev-parse", "HEAD"])
    susp = host.revisar_suspension()
    datos = {
        "fecha_utc": ahora(),
        "host": host_info,
        "windows_powercfg": {"plan": host.plan_energia(), "ajustes_ac": susp["valores"],
                             "problemas": susp["problemas"], "no_comprobado": susp["no_comprobado"]},
        "servicio_mongodb_windows": host.estado_mongodb_windows(),
        "wsl": {"version": wsl_version, "distribuciones": wsl_lista,
                "wslconfig": wslconfig.read_text(encoding="utf-8") if wslconfig.exists() else None},
        "docker": {"cliente": docker_version.get("Client", {}).get("Version"),
                   "servidor": docker_version.get("Server", {}).get("Version"),
                   "compose": compose, "info": info_sel},
        "vm_docker": meminfo_vm,
        "imagenes": imagenes,
        "versiones_motor": {"mongo": config.VERSION_ESPERADA["mongo"],
                            "postgres": config.VERSION_ESPERADA["postgres"]},
        "python": platform.python_version(),
        "paquetes_venv": pip.splitlines(),
        "commit_codigo": commit,
    }
    guardar(crudo / "wsl_version.txt", wsl_version)
    guardar(crudo / "wsl_l_v.txt", wsl_lista)
    guardar(crudo / "docker_version.json", docker_version)
    guardar(crudo / "docker_info.json", docker_info)
    guardar(crudo / "vm_meminfo_uname.txt", meminfo_vm)
    guardar(crudo / "pip_freeze.txt", pip)
    guardar(D_ENT / "entorno.json", datos)
    return datos


# --------------------------------------------------------------------------
# Verificación
# --------------------------------------------------------------------------
def limites() -> dict:
    res = {}
    for perfil in config.PERFILES:
        for motor in ("mongo", "postgres"):
            info = contenedores.encender_solo(motor, perfil, recrear=True)
            p = config.leer_perfil(perfil)
            res[f"{motor}_{perfil}"] = {
                "perfil": p, "limites_reales": info["limites"],
                "esperado": {"memory_max": int(p["MEM_LIMIT"].rstrip("m")) * 2**20,
                             "memory_swap_max": "0",
                             "cpu_max": f"{int(float(p['CPUS']) * 100000)} 100000"},
                "identidad": {k: v for k, v in info.items() if k not in ("limites", "perfil")},
                "coincide": True,   # encender_solo aborta si no coincide
            }
            contenedores.apagar(motor)
    return res


def documentos_y_tamanos() -> dict:
    stats = json.loads((config.RAIZ / "data" / "ventas.stats.json").read_text(encoding="utf-8"))
    res = {"dataset": {"archivo": "data/ventas.ndjson (no se versiona; se regenera con la semilla)",
                       "sha256": sha256(DATASET), "bytes": DATASET.stat().st_size, **stats}}
    rng = random.Random(20261101)
    muestra = [f"VNT{rng.randint(1, 500_000):06d}" for _ in range(2000)]
    contenedores.encender_solo("mongo", "PROFILE_A_BASELINE")
    cli, _ = identidad.conectar_mongo()
    col = cli[config.BASE][config.COLECCION]
    s = cli[config.BASE].command("collStats", config.COLECCION)
    docs_m = {d["_id"]: d for d in col.find({"_id": {"$in": muestra}})}
    res["mongo"] = {"documentos": col.count_documents({}), "indices": sorted(col.index_information()),
                    "tamano_medio_bson_bytes": s["avgObjSize"], "tamano_datos_bytes": s["size"],
                    "items_por_venta_media": next(col.aggregate([{"$group": {
                        "_id": None, "m": {"$avg": {"$size": "$items"}}}}]))["m"]}
    cli.close()
    contenedores.apagar("mongo")
    contenedores.encender_solo("postgres", "PROFILE_A_BASELINE")
    conn, _ = identidad.conectar_postgres()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*), round(avg(pg_column_size(data)), 1), "
                    "round(avg(jsonb_array_length(data->'items')), 4) FROM ventas")
        n, tam, items = cur.fetchone()
        cur.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'ventas' ORDER BY 1")
        indices = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT data FROM ventas WHERE data->>'_id' = ANY(%s)", (muestra,))
        docs_p = {d["_id"]: d for (d,) in cur.fetchall()}
    conn.close()
    contenedores.apagar("postgres")
    res["postgres"] = {"documentos": n, "indices": indices, "tamano_medio_jsonb_bytes": float(tam),
                       "items_por_venta_media": float(items)}
    iguales = sum(docs_m.get(k) == docs_p.get(k) for k in set(muestra))
    res["muestra_identica"] = {"ids_distintos": len(set(muestra)), "iguales": iguales,
                               "encontrados_mongo": len(docs_m), "encontrados_postgres": len(docs_p)}
    return res


def drop_caches() -> dict:
    cmd = ("grep -E '^(Cached|Buffers|MemFree):' /proc/meminfo; sync; "
           "echo 3 > /proc/sys/vm/drop_caches && echo DROP_OK; "
           "grep -E '^(Cached|Buffers|MemFree):' /proc/meminfo")
    salida = sh(["docker", "run", "--rm", "--privileged", "--entrypoint", "sh", host.IMAGEN_FLUSH,
                 "-c", cmd])
    lineas = salida.splitlines()
    def kb(bloque, clave):
        return next(int(l.split()[1]) for l in bloque if l.startswith(clave))
    i = lineas.index("DROP_OK") if "DROP_OK" in lineas else None
    res = {"salida": salida, "funciona": i is not None}
    if i is not None:
        res["cached_kb_antes"], res["cached_kb_despues"] = kb(lineas[:i], "Cached"), kb(lineas[i:], "Cached")
    return res


def consultas() -> dict:
    m = check_queries.correr_motor("mongo", "PROFILE_A_BASELINE")
    p = check_queries.correr_motor("postgres", "PROFILE_A_BASELINE")
    comp = check_queries.comparar(m, p)
    q1m, q1p = m["consultas"]["Q1"]["datos"], p["consultas"]["Q1"]["datos"]
    cm, cp = m["consultas"]["Q1_CONTEO"]["datos"], p["consultas"]["Q1_CONTEO"]["datos"]
    with (D_VER / "q1_grupos.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["tipo_pago", "proveedor_id", "articulos_mongo", "articulos_postgres",
                    "suma_mongo", "suma_postgres", "dif_suma", "promedio_mongo",
                    "promedio_postgres", "dif_promedio"])
        for k in sorted(set(q1m) | set(q1p)):
            sm, am = q1m.get(k, (None, None))
            sp, ap = q1p.get(k, (None, None))
            w.writerow([*k, cm.get(k), cp.get(k), sm, sp,
                        abs(sm - sp) if None not in (sm, sp) else None, am, ap,
                        abs(am - ap) if None not in (am, ap) else None])
    for q in ("Q2", "Q2_RELAXED"):
        lm, lp = m["consultas"][q]["datos"], p["consultas"][q]["datos"]
        with (D_VER / f"{q.lower()}_top1000.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["posicion", "_id_mongo", "total_mongo", "_id_postgres", "total_postgres", "igual"])
            for i in range(max(len(lm), len(lp))):
                a = lm[i] if i < len(lm) else (None, None)
                b = lp[i] if i < len(lp) else (None, None)
                w.writerow([i + 1, *a, *b, a == b])
    tiempos = {mot: {q: v["consultas"][q]["segundos"] for q in check_queries.CONSULTAS}
               for mot, v in (("mongo", m), ("postgres", p))}
    return {"perfil": "PROFILE_A_BASELINE", "clientes": 1, "comparacion": comp, "tiempos_s": tiempos,
            "identidad": {"mongo": m["identidad"], "postgres": p["identidad"]}}


def main() -> int:
    D_VER.mkdir(parents=True, exist_ok=True)
    inicio = ahora()
    print("[F2] entorno ...", flush=True)
    ent = entorno()
    print("[F2] límites A/B/C ...", flush=True)
    lim = limites()
    print("[F2] drop_caches ...", flush=True)
    dc = drop_caches()
    print("[F2] documentos, tamaños y muestra ...", flush=True)
    doc = documentos_y_tamanos()
    print("[F2] Q1/Q2 en perfil A con 1 cliente ...", flush=True)
    con = consultas()
    guardar(D_VER / "limites.json", lim)
    guardar(D_VER / "drop_caches.json", dc)
    guardar(D_VER / "documentos.json", doc)
    guardar(D_VER / "consultas.json", con)

    c = con["comparacion"]
    tam_json = doc["dataset"]["tamano_medio_bytes"]
    condiciones = [
        ("500 000 ventas en MongoDB", doc["mongo"]["documentos"] == 500_000, f"{doc['mongo']['documentos']:,}"),
        ("500 000 ventas en PostgreSQL", doc["postgres"]["documentos"] == 500_000, f"{doc['postgres']['documentos']:,}"),
        ("≥ 3 niveles de anidamiento", doc["dataset"]["niveles_anidamiento"] >= 3,
         f"{doc['dataset']['niveles_anidamiento']} (items → producto → proveedor → campos)"),
        ("~3 artículos por venta", 2.8 <= doc["mongo"]["items_por_venta_media"] <= 3.2,
         f"Mongo {doc['mongo']['items_por_venta_media']:.4f}; PostgreSQL {doc['postgres']['items_por_venta_media']:.4f}"),
        (f"Tamaño por venta ≥ {MIN_KB} KB", tam_json / 1000 >= MIN_KB and tam_json / 1024 >= MIN_KB,
         f"JSON {tam_json} B ({tam_json / 1000:.3f} kB; {tam_json / 1024:.3f} KiB); BSON "
         f"{doc['mongo']['tamano_medio_bson_bytes']} B; JSONB {doc['postgres']['tamano_medio_jsonb_bytes']} B"),
        ("Mismo archivo en ambos motores (muestra de documentos)",
         doc["muestra_identica"]["iguales"] == doc["muestra_identica"]["ids_distintos"],
         f"{doc['muestra_identica']['iguales']}/{doc['muestra_identica']['ids_distintos']} documentos idénticos"),
        ("Q1: mismos grupos", c["q1_mismos_grupos"], f"{c['q1_grupos']} grupos"),
        ("Q1: conteo de artículos por grupo exacto", c["q1_conteos_exactos"], f"{c['q1_articulos_total']:,} artículos"),
        ("Q1: sumas y promedios con tolerancia de 1 centavo", c["q1_dentro_tolerancia"],
         f"dif. máx. suma {c['q1_dif_max_suma']:.2e}; promedio {c['q1_dif_max_promedio']:.2e}"),
        ("Q2: Top-1000 idéntico entre motores", c["q2_identica_entre_motores"] and c["q2_filas"] == 1000,
         f"{c['q2_filas']} filas; primero {c['q2_primero']}"),
        ("Q2_RELAXED: idéntico entre motores", c["q2_relaxed_identica_entre_motores"], ""),
        ("Q1 y Q2 terminan en el Perfil A con 1 cliente", all(
            t is not None for t in [*con["tiempos_s"]["mongo"].values(), *con["tiempos_s"]["postgres"].values()]),
         "; ".join(f"{mot} " + ", ".join(f"{q} {t:.2f} s" for q, t in v.items())
                   for mot, v in con["tiempos_s"].items())),
        ("Límites reales A/B/C = perfil, en ambos motores", all(v["coincide"] for v in lim.values()),
         "; ".join(f"{k}: {v['limites_reales']['memory_max'] // 2**20} MiB, swap {v['limites_reales']['memory_swap_max']}, "
                   f"cpu {v['limites_reales']['cpu_max']}" for k, v in lim.items())),
        ("drop_caches funciona en la VM", dc["funciona"],
         f"Cached {dc.get('cached_kb_antes')} kB → {dc.get('cached_kb_despues')} kB"),
    ]
    todo = all(ok for _, ok, _ in condiciones)
    md = ["# Verificación F2 (antes del piloto)", "",
          f"Fecha: {inicio} a {ahora()} (UTC). Código: commit `{ent['commit_codigo'][:12]}`.  ",
          "Generado con `runner/verificar_f2.py`. Ninguna de estas ejecuciones es una medición.", "",
          f"**Resultado: {'TODAS LAS CONDICIONES SE CUMPLEN' if todo else 'HAY CONDICIONES QUE NO SE CUMPLEN'}**", "",
          "| Condición | Resultado | Detalle |", "|---|---|---|"]
    md += [f"| {n} | {'cumple' if ok else '**NO cumple**'} | {d} |" for n, ok, d in condiciones]
    md += ["", "## Notas", "",
           "- Tolerancia y comparación según D-016: conteos exactos, sumas y promedios con tolerancia de "
           "1 centavo (Mongo suma en double; PostgreSQL en numeric), lista de Q2 idéntica (`_id` y `total`).",
           "- \"Terminan con 1 cliente\": cada consulta se ejecutó una vez, de forma secuencial, con el Perfil A; "
           "los tiempos son de una sola ejecución y no son mediciones.",
           "- El tamaño por venta se toma del JSON canónico generado (el mismo archivo para ambos motores); "
           "también se reportan el BSON de Mongo y el JSONB de PostgreSQL.",
           f"- Servicio MongoDB de Windows durante F2: {ent['servicio_mongodb_windows']} (no afecta la "
           "verificación: todo se conecta al contenedor con verificación de identidad, D-021).",
           "- Los planes de Q1 y Q2 están en `evidence/plans/` y no se repiten aquí.", "",
           "## Archivos", "",
           "- `documentos.json`: conteos, índices, tamaños, hash del dataset y muestra comparada",
           "- `consultas.json`: comparación completa, tiempos e identidad de los servidores",
           "- `q1_grupos.csv`: los 90 grupos de Q1 con los valores de ambos motores y sus diferencias",
           "- `q2_top1000.csv`, `q2_relaxed_top1000.csv`: las 1000 filas de ambos motores, lado a lado",
           "- `limites.json`: memory.max, memory.swap.max y cpu.max reales por perfil y motor",
           "- `drop_caches.json`: salida de la prueba de flush en la VM"]
    guardar(D_VER / "README.md", "\n".join(md))

    h, d, w = ent["host"], ent["docker"], ent["wsl"]
    md_e = ["# Entorno de ejecución (sección 1 del reporte)", "",
            f"Capturado: {ent['fecha_utc']} (UTC) con `runner/verificar_f2.py`.", "",
            "## Host", "",
            f"- Equipo: {h['fabricante']} {h['modelo']} (laptop; batería: {'sí' if h['bateria'] else 'no'})",
            f"- CPU: {h['cpu']}, {h['nucleos']} núcleos / {h['hilos']} hilos, {h['mhz_max']} MHz máx.",
            f"- RAM: {int(h['ram_bytes']) / 2**30:.1f} GiB ({h['ram_bytes']} bytes)",
            f"- Disco: {', '.join(h['discos']) if isinstance(h['discos'], list) else h['discos']}",
            f"- Sistema: {h['so']} {h['version']} (build {h['build']}, {h['arquitectura']})",
            f"- Plan de energía: {ent['windows_powercfg']['plan']}",
            f"- powercfg en corriente alterna: {ent['windows_powercfg']['ajustes_ac']}",
            "", "## WSL", "", "```", w["version"], "```", "", "```", w["distribuciones"], "```", "",
            f"- `.wslconfig`: {'ver entorno.json' if w['wslconfig'] else 'no existe (valores por defecto de WSL)'}",
            "", "## Docker", "",
            f"- Docker Engine {d['servidor']} (cliente {d['cliente']}), Compose {d['compose']}",
            f"- {d['info']['OperatingSystem']}, kernel {d['info']['KernelVersion']}, {d['info']['Architecture']}",
            f"- cgroups v{d['info']['CgroupVersion']} (driver {d['info']['CgroupDriver']}), "
            f"almacenamiento {d['info']['Driver']}",
            f"- VM de Docker: {d['info']['NCPU']} CPU, {int(d['info']['MemTotal']) / 2**30:.2f} GiB",
            "", "```", ent["vm_docker"], "```", "", "## Imágenes (etiqueta y digest)", "",
            "| Imagen | Digest |", "|---|---|"]
    md_e += [f"| `{k}` | `{v[0].split('@')[1] if v else '—'}` |" for k, v in ent["imagenes"].items()]
    md_e += ["", f"- Versiones que reportan los motores: MongoDB {con['identidad']['mongo']['version']}, "
             f"PostgreSQL {con['identidad']['postgres']['version']}",
             f"- Python {ent['python']}; paquetes: {', '.join(ent['paquetes_venv'])}",
             f"- Commit del código: `{ent['commit_codigo']}`", "",
             "Datos crudos en `entorno.json` y `crudo/`."]
    guardar(D_ENT / "README.md", "\n".join(md_e))
    print("\n".join(f"{'OK ' if ok else 'NO '} {n}: {d}" for n, ok, d in condiciones))
    return 0 if todo else 1


if __name__ == "__main__":
    sys.exit(main())
