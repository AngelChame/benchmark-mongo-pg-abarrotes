# Entorno de ejecución (sección 1 del reporte)

Capturado (foto del estado durante F2, antes del piloto): 2026-11-01T05:59:47Z (UTC) con `runner/verificar_f2.py`.

## Host

- Equipo: HP Victus by HP Gaming Laptop 15-fb2xxx (laptop; batería: sí)
- CPU: AMD Ryzen 5 8645HS w/ Radeon 760M Graphics, 6 núcleos / 12 hilos, 4301 MHz máx.
- RAM: 15.3 GiB (16390729728 bytes)
- Disco: WD PC SN560 SDDPNQE-1T00-2006 (1024203640320 bytes)
- Sistema: Microsoft Windows 11 Home Single Language 10.0.26200 (build 26200, 64 bits)
- Plan de energía: GUID de plan de energía: 381b4222-f694-41f0-9685-ff5bb260df2e  (Equilibrado)
- powercfg en corriente alterna (antes de corregir la suspensión; el orquestador vuelve a revisarlo al arrancar y guarda el estado en `revisiones_entorno/`): {'suspender_tras_s': 18000, 'hibernar_tras_s': 0, 'accion_tapa': None}

## WSL

```
Versión de WSL: 2.6.3.0
Versión de kernel: 6.6.87.2-1
Versión de WSLg: 1.0.71
Versión de MSRDC: 1.2.6353
Versión de Direct3D: 1.611.1-81528511
Versión de DXCore: 10.0.26100.1-240331-1435.ge-release
Versión de Windows: 10.0.26200.9711
```

```
NAME              STATE           VERSION
* docker-desktop    Running         2
```

- `.wslconfig`: no existe (valores por defecto de WSL)

## Docker

- Docker Engine 29.9.2 (cliente 29.9.2), Compose 5.6.1
- Docker Desktop, kernel 6.6.87.2-microsoft-standard-WSL2, x86_64
- cgroups v2 (driver cgroupfs), almacenamiento overlayfs
- VM de Docker: 12 CPU, 7.39 GiB

```
MemTotal:        7748816 kB
MemFree:         6680688 kB
MemAvailable:    6940572 kB
SwapTotal:       2097152 kB
SwapFree:        2096372 kB
12
Linux cb6ba1d44854 6.6.87.2-microsoft-standard-WSL2 #1 SMP PREEMPT_DYNAMIC Thu Jun  5 18:30:46 UTC 2025 x86_64 GNU/Linux
```

## Imágenes (etiqueta y digest)

| Imagen | Digest |
|---|---|
| `mongo:9.0.4` | `sha256:452bdaf2127b8ff851e31410a267809b9829eded219979f2210e7c7efb85a058` |
| `postgres:18.6` | `sha256:3a4264520e121176619f54ff867d12774bc81e95e7b84db58d63bccdb6144b81` |
| `prom/prometheus:v3.15.1` | `sha256:fa3302279710be2f21a8301ec17a8b4f2301fd829bd113839b3b6b02b6f191a2` |
| `prom/node-exporter:v1.12.1` | `sha256:1b4e4438faca4dd7e001dd445d161a4a2091b0fededa84093b3a8dfeae1f1be0` |
| `ghcr.io/google/cadvisor:v0.60.6` | `sha256:b8e7d1093144fd088f425ff003d75a4aa405de075db78dae3bc563730b1bd07a` |

- Versiones que reportan los motores: MongoDB 9.0.4, PostgreSQL 18.6 (Debian 18.6-1.pgdg13+2)
- Python 3.12.10; paquetes: dnspython==2.9.1, psycopg==3.3.6, psycopg-binary==3.3.6, pymongo==4.19.0, typing_extensions==4.16.0, tzdata==2026.6
- Commit del código (HEAD al ejecutar; `runner/verificar_f2.py` entra en el commit siguiente, junto con esta evidencia): `ad5e6ddffa919ae1a2599e6038b52d70e62c78ac`

Datos crudos en `entorno.json` y `crudo/`.
