"""Capa de proveedores de precio.

Cada proveedor expone fetch(url|asin) -> Quote. Cambiar de scraper propio a
Keepa es cambiar la variable de entorno PRICE_PROVIDER, no el resto del codigo.
"""
from __future__ import annotations

from ..config import env
from .base import Quote


def get_provider(name: str | None = None):
    name = (name or env("PRICE_PROVIDER", "amazon_scraper")).lower()
    if name == "amazon_scraper":
        from .amazon_scraper import AmazonScraper
        return AmazonScraper()
    if name == "keepa":
        from .keepa import KeepaProvider
        return KeepaProvider()
    raise ValueError(f"Proveedor desconocido: {name}")


__all__ = ["get_provider", "Quote"]
