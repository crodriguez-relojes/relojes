"""Proveedor Keepa (opcional, de pago).

Activacion:
    1. Contrata Keepa API y pon KEEPA_API_KEY en .env
    2. Cambia PRICE_PROVIDER=keepa en .env o en los secrets de GitHub

Ventaja: historico real de anios desde el primer dia, sin riesgo de bloqueo.
Este modulo usa la API REST directa (no requiere el paquete `keepa`).
"""
from __future__ import annotations

import requests

from ..config import env
from .base import PriceProvider, Quote

KEEPA_ENDPOINT = "https://api.keepa.com/product"
DOMAIN_ID = {"amazon.com": 1, "amazon.co.uk": 2, "amazon.de": 3,
             "amazon.es": 9, "amazon.com.mx": 11}


class KeepaProvider(PriceProvider):
    name = "keepa"

    def __init__(self, domain: str = "amazon.com") -> None:
        self.key = env("KEEPA_API_KEY")
        if not self.key:
            raise RuntimeError("Falta KEEPA_API_KEY en .env")
        self.domain_id = DOMAIN_ID.get(domain, 1)

    def fetch(self, asin: str, url: str) -> Quote:
        try:
            resp = requests.get(
                KEEPA_ENDPOINT,
                params={"key": self.key, "domain": self.domain_id,
                        "asin": asin, "stats": 1},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            return Quote(asin=asin, price=None, error=f"keepa: {exc}")

        products = data.get("products") or []
        if not products:
            return Quote(asin=asin, price=None, error="keepa: ASIN sin datos")

        p = products[0]
        stats = p.get("stats") or {}
        current = stats.get("current") or []
        # Indices Keepa: 0=AMAZON, 1=NEW, 18=BUY_BOX. Precios en centavos, -1 = sin dato.
        price = None
        for idx in (18, 0, 1):
            if len(current) > idx and current[idx] and current[idx] > 0:
                price = current[idx] / 100.0
                break

        return Quote(asin=asin, price=price, currency="USD",
                     in_stock=price is not None,
                     title=p.get("title", ""),
                     error="" if price else "keepa: sin precio actual")

    def polite_pause(self) -> None:
        return None
