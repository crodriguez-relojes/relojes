"""Lee las respuestas de un Formulario de Google y agrega los relojes nuevos.

Como funciona:
    1. Creas un Google Form con un campo para el link de Amazon
       (y opcionalmente uno para el precio objetivo).
    2. Sus respuestas caen en una Hoja de calculo.
    3. Publicas esa hoja como CSV (Archivo > Compartir > Publicar en la web).
    4. Pones ese URL en la variable FORM_CSV_URL.

Cada corrida diaria lee la hoja y agrega al repositorio los ASIN que aun
no esten. No borra nada: quitar un reloj sigue siendo decision tuya.

El parseo es deliberadamente tolerante: no asume en que columna quedo cada
campo, porque eso depende de como el usuario haya redactado las preguntas.
Busca en cada fila lo que parezca un link de Amazon y lo que parezca un precio.
"""
from __future__ import annotations

import csv
import io
import re

import requests

from .config import WATCHES_CSV, env
from .db import extract_asin, read_watches

# Limite de seguridad: si alguien difunde el formulario, esto evita que una
# sola corrida intente monitorear miles de productos.
MAX_NUEVOS_POR_CORRIDA = 25

PRECIO_RE = re.compile(r"^\s*\$?\s*([0-9]+(?:[.,][0-9]{1,2})?)\s*$")

# Los links que se comparten desde la app de Amazon no traen el ASIN:
# hay que seguir la redireccion para averiguar a que producto apuntan.
CORTOS = ("a.co/", "amzn.to/", "amzn.eu/", "amzn.asia/")


def _resolver_corto(url: str) -> str:
    """Sigue la redireccion de un link corto y devuelve el URL final."""
    try:
        resp = requests.get(url, timeout=20, allow_redirects=True,
                            headers={"User-Agent": "Mozilla/5.0"})
        return resp.url
    except Exception:  # noqa: BLE001
        return url


def _asin_de(celda: str) -> str | None:
    asin = extract_asin(celda)
    if asin:
        return asin
    if celda.startswith("http") and any(c in celda for c in CORTOS):
        return extract_asin(_resolver_corto(celda))
    return None


def _precio(texto: str) -> float | None:
    m = PRECIO_RE.match(texto or "")
    if not m:
        return None
    try:
        valor = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    # Un precio de reloj plausible; descarta timestamps y numeros sueltos
    return valor if 1 <= valor <= 100000 else None


def _filas(url: str) -> list[list[str]]:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return list(csv.reader(io.StringIO(resp.text)))


def sincronizar(url: str | None = None) -> int:
    """Agrega a watches.csv los relojes nuevos del formulario. Devuelve cuantos."""
    url = url or env("FORM_CSV_URL")
    if not url:
        return 0

    try:
        filas = _filas(url)
    except Exception as exc:  # noqa: BLE001
        print(f"  [!] No se pudo leer el formulario: {exc}")
        return 0

    existentes = {p.asin for p in read_watches()}
    nuevos: list[tuple[str, float | None, str]] = []
    vistos: set[str] = set()

    for fila in filas[1:]:            # la primera fila son los encabezados
        asin = None
        objetivo = None
        quien = ""
        # La columna 0 de Google Forms es siempre la marca de tiempo: se salta
        # para que no acabe interpretada como el nombre de quien sugirio.
        for celda in fila[1:]:
            celda = (celda or "").strip()
            if not celda:
                continue
            if asin is None:
                asin = _asin_de(celda)
                if asin:
                    continue
            if objetivo is None:
                objetivo = _precio(celda)
                if objetivo is not None:
                    continue
            # Texto corto que no es link ni precio: probablemente el nombre
            if not quien and len(celda) < 40 and "@" not in celda \
                    and not celda.startswith("http"):
                quien = celda

        if not asin or asin in existentes or asin in vistos:
            continue
        vistos.add(asin)
        nuevos.append((asin, objetivo, quien))

    if not nuevos:
        return 0

    if len(nuevos) > MAX_NUEVOS_POR_CORRIDA:
        print(f"  [!] El formulario trae {len(nuevos)} relojes nuevos; se agregan "
              f"los primeros {MAX_NUEVOS_POR_CORRIDA} y el resto en la proxima corrida.")
        nuevos = nuevos[:MAX_NUEVOS_POR_CORRIDA]

    with open(WATCHES_CSV, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        for asin, objetivo, quien in nuevos:
            w.writerow([
                asin,
                asin,                                   # el nombre se lee de Amazon
                f"https://www.amazon.com/dp/{asin}",
                objetivo if objetivo is not None else "",
                "true",
                "",
                f"sugerido por {quien}" if quien else "sugerido por formulario",
            ])
            print(f"  [+] Nuevo desde el formulario: {asin}"
                  + (f" (objetivo ${objetivo:,.2f})" if objetivo else ""))

    return len(nuevos)
