#!/usr/bin/env python3
"""Generador del dataset sintético de ventas de abarrotes.

Produce un único archivo NDJSON (una venta por línea) que se carga tal cual en
MongoDB y en PostgreSQL JSONB. Todo es sintético y reproducible: la misma
semilla genera exactamente el mismo archivo.

Modelo (colección `ventas` del proyecto de abarrotes, más el snapshot del
producto en cada artículo):

    _id, folio, fecha (ISO-8601 UTC), usuario_id, tipo_pago, total, es_fiado,
    cliente_id, items[ {producto_id, nombre, cantidad, precio_unitario,
    subtotal, producto{codigo_barras, gramos_contenido,
    proveedor{id, empresa}}} ]

Uso:
    python synthetic_data_generator.py                  # 500 000 ventas
    python synthetic_data_generator.py --n 2000 --out data/muestra.ndjson
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEMILLA = 20261101
N_VENTAS = 500_000
FECHA_INICIO = datetime(2025, 1, 1, tzinfo=timezone.utc)
FECHA_FIN = datetime(2026, 10, 31, 23, 59, 59, tzinfo=timezone.utc)
N_PROVEEDORES = 30
N_PRODUCTOS = 600
N_CAJEROS = 6
N_CLIENTES_FIADO = 800
PROB_PRECIO_ALTO = 0.45          # fracción de productos con precio > $50
TIPOS_PAGO = (("EFECTIVO", 0.60), ("TRANSFERENCIA", 0.25), ("FIADO", 0.15))

# Espacio de nombres fijo para derivar el UUID de PostgreSQL a partir de `_id`.
# No cambiarlo: el `id` de cada fila depende de él.
NAMESPACE_VENTAS = uuid.UUID("6f1c2a3e-9b4d-5e7f-8a1b-2c3d4e5f6a7b")


def uuid_de_venta(id_venta: str) -> str:
    """UUID determinista (uuid5) de una venta; columna `id` en PostgreSQL."""
    return str(uuid.uuid5(NAMESPACE_VENTAS, id_venta))


# --------------------------------------------------------------------------
# Catálogo (fijo para una semilla)
# --------------------------------------------------------------------------
_EMPRESAS = [
    "Distribuidora del Sureste", "Abarrotera La Central", "Lácteos Chiapanecos",
    "Grupo Panificador del Valle", "Bebidas y Refrescos del Istmo",
    "Comercializadora Tuxtla", "Granos y Semillas Soconusco",
    "Productos de Limpieza El Brillo", "Botanas La Ceiba", "Café de Altura Chiapas",
    "Embutidos San Cristóbal", "Aceites y Grasas del Golfo",
    "Distribuidora Cancuc", "Dulces Típicos Chiapa de Corzo",
    "Higiene Personal del Sur", "Conservas La Huerta", "Harinas El Molino",
    "Abastos Comitán", "Lácteos Los Altos", "Refrescos Mezcalapa",
    "Comercial Tapachula", "Mayoreo Ocosingo", "Distribuidora Palenque",
    "Galletas y Pastas La Fortuna", "Productos Avícolas Cintalapa",
    "Detergentes del Pacífico", "Especias y Condimentos Yajalón",
    "Frutas Secas Villaflores", "Agua Purificada Arriaga",
    "Mercantil Pijijiapan",
]

_PRODUCTOS_BASE = [
    "Arroz", "Frijol negro", "Frijol pinto", "Azúcar estándar", "Sal de mesa",
    "Aceite vegetal", "Harina de trigo", "Harina de maíz", "Pasta para sopa",
    "Atún en agua", "Sardina en tomate", "Leche entera", "Leche deslactosada",
    "Yogur natural", "Queso fresco", "Crema ácida", "Mantequilla", "Huevo blanco",
    "Café molido", "Café soluble", "Chocolate de mesa", "Galletas marías",
    "Galletas de chocolate", "Pan de caja", "Tortillinas", "Refresco de cola",
    "Refresco de naranja", "Agua natural", "Jugo de mango", "Papas fritas",
    "Cacahuates salados", "Chile en lata", "Puré de tomate", "Mayonesa",
    "Mostaza", "Salsa picante", "Consomé de pollo", "Avena en hojuelas",
    "Cereal de maíz", "Jabón de barra", "Detergente en polvo", "Suavizante de ropa",
    "Cloro", "Limpiador multiusos", "Papel higiénico", "Servilletas",
    "Pasta dental", "Champú", "Desodorante", "Toallas femeninas",
]
_MARCAS = ["Selecta", "La Costeña", "Don Pancho", "El Sureño", "Doña Mary",
           "Del Valle Verde", "Premium", "Económica", "La Granja", "Tradicional"]
_PRESENTACIONES = [(100, "100 g"), (250, "250 g"), (400, "400 g"), (500, "500 g"),
                   (900, "900 g"), (1000, "1 kg"), (2000, "2 kg"), (355, "355 ml"),
                   (600, "600 ml"), (1000, "1 L"), (2000, "2 L"), (3000, "3 L")]


def _ean13(rng: random.Random) -> str:
    cuerpo = "750" + "".join(str(rng.randrange(10)) for _ in range(9))
    suma = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(cuerpo))
    return cuerpo + str((10 - suma % 10) % 10)


def construir_catalogo(rng: random.Random) -> list[dict]:
    proveedores = [{"id": f"PRV{i + 1:03d}", "empresa": _EMPRESAS[i]}
                   for i in range(N_PROVEEDORES)]
    caros = set(rng.sample(range(N_PRODUCTOS), round(PROB_PRECIO_ALTO * N_PRODUCTOS)))
    catalogo = []
    for i in range(N_PRODUCTOS):
        gramos, etiqueta = rng.choice(_PRESENTACIONES)
        nombre = f"{rng.choice(_PRODUCTOS_BASE)} {rng.choice(_MARCAS)} {etiqueta}"
        if i in caros:
            precio = round(rng.uniform(50.01, 180.00), 2)
        else:
            precio = round(rng.uniform(8.00, 50.00), 2)
        catalogo.append({
            "producto_id": f"PRD{i + 1:05d}",
            "nombre": nombre,
            "precio_unitario": precio,
            "producto": {
                "codigo_barras": _ean13(rng),
                "gramos_contenido": gramos,
                "proveedor": dict(rng.choice(proveedores)),
            },
        })
    return catalogo


# --------------------------------------------------------------------------
# Ventas
# --------------------------------------------------------------------------
def _num_articulos(rng: random.Random) -> int:
    # 1 + Binomial(9, 2/9): rango 1..10, media 3,0
    return 1 + sum(rng.random() < 2 / 9 for _ in range(9))


def _cantidad(rng: random.Random) -> int:
    return rng.choices((1, 2, 3, 4, 5), weights=(55, 25, 10, 6, 4))[0]


def generar(n: int, semilla: int, salida: Path) -> dict:
    rng = random.Random(semilla)
    catalogo = construir_catalogo(rng)
    cajeros = [f"USR{i + 1:03d}" for i in range(N_CAJEROS)]
    clientes = [f"CLI{i + 1:05d}" for i in range(N_CLIENTES_FIADO)]
    nombres_pago = [t for t, _ in TIPOS_PAGO]
    pesos_pago = [p for _, p in TIPOS_PAGO]

    # Folios en orden cronológico, como en una tienda real.
    rango_s = int((FECHA_FIN - FECHA_INICIO).total_seconds())
    segundos = sorted(rng.randrange(rango_s + 1) for _ in range(n))

    stats = {"tipo_pago": Counter(), "articulos": Counter(), "items": 0,
             "items_precio_mayor_50": 0, "bytes": 0, "bytes_min": None,
             "bytes_max": 0, "q2_candidatas": 0}
    salida.parent.mkdir(parents=True, exist_ok=True)
    with salida.open("w", encoding="utf-8", newline="\n") as f:
        for folio, seg in enumerate(segundos, start=1):
            fecha = (FECHA_INICIO + timedelta(seconds=seg)).strftime("%Y-%m-%dT%H:%M:%SZ")
            tipo_pago = rng.choices(nombres_pago, weights=pesos_pago)[0]
            es_fiado = tipo_pago == "FIADO"
            items = []
            for _ in range(_num_articulos(rng)):
                p = rng.choice(catalogo)
                cantidad = _cantidad(rng)
                items.append({
                    "producto_id": p["producto_id"],
                    "nombre": p["nombre"],
                    "cantidad": cantidad,
                    "precio_unitario": p["precio_unitario"],
                    "subtotal": round(cantidad * p["precio_unitario"], 2),
                    "producto": {
                        "codigo_barras": p["producto"]["codigo_barras"],
                        "gramos_contenido": p["producto"]["gramos_contenido"],
                        "proveedor": dict(p["producto"]["proveedor"]),
                    },
                })
                stats["items_precio_mayor_50"] += p["precio_unitario"] > 50
            venta = {
                "_id": f"VNT{folio:06d}",
                "folio": folio,
                "fecha": fecha,
                "usuario_id": rng.choice(cajeros),
                "tipo_pago": tipo_pago,
                "total": round(sum(i["subtotal"] for i in items), 2),
                "es_fiado": es_fiado,
                "cliente_id": rng.choice(clientes) if es_fiado else None,
                "items": items,
            }
            linea = json.dumps(venta, ensure_ascii=False, separators=(",", ":"))
            f.write(linea + "\n")

            tam = len(linea.encode("utf-8"))
            stats["bytes"] += tam
            stats["bytes_min"] = tam if stats["bytes_min"] is None else min(stats["bytes_min"], tam)
            stats["bytes_max"] = max(stats["bytes_max"], tam)
            stats["tipo_pago"][tipo_pago] += 1
            stats["articulos"][len(items)] += 1
            stats["items"] += len(items)
            stats["q2_candidatas"] += (not es_fiado) and fecha >= "2026-01-01T00:00:00Z"
    return stats


def resumen(stats: dict, n: int, semilla: int, salida: Path, segundos: float) -> dict:
    return {
        "archivo": salida.as_posix(),
        "ventas": n,
        "semilla": semilla,
        "python": platform.python_version(),
        "segundos_generacion": round(segundos, 1),
        "tamano_medio_bytes": round(stats["bytes"] / n, 1),
        "tamano_min_bytes": stats["bytes_min"],
        "tamano_max_bytes": stats["bytes_max"],
        "articulos_por_venta_media": round(stats["items"] / n, 3),
        "articulos_por_venta_distribucion": dict(sorted(stats["articulos"].items())),
        "articulos_total": stats["items"],
        "fraccion_articulos_precio_mayor_50": round(stats["items_precio_mayor_50"] / stats["items"], 4),
        "tipo_pago": {k: round(v / n, 4) for k, v in sorted(stats["tipo_pago"].items())},
        "ventas_filtro_q2": stats["q2_candidatas"],
        "niveles_anidamiento": 4,
        "rango_fechas": [FECHA_INICIO.strftime("%Y-%m-%dT%H:%M:%SZ"),
                         FECHA_FIN.strftime("%Y-%m-%dT%H:%M:%SZ")],
        "proveedores": N_PROVEEDORES,
        "productos_catalogo": N_PRODUCTOS,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=N_VENTAS)
    ap.add_argument("--seed", type=int, default=SEMILLA)
    ap.add_argument("--out", type=Path, default=Path("data/ventas.ndjson"))
    ap.add_argument("--stats", type=Path, default=None,
                    help="JSON con las estadísticas (por defecto, junto al archivo)")
    args = ap.parse_args()

    t0 = time.perf_counter()
    stats = generar(args.n, args.seed, args.out)
    info = resumen(stats, args.n, args.seed, args.out, time.perf_counter() - t0)
    destino = args.stats or args.out.with_suffix(".stats.json")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
