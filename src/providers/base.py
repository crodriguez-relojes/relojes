from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Quote:
    """Resultado de consultar el precio de un producto."""
    asin: str
    price: float | None          # None = no se pudo leer o sin stock
    currency: str = "USD"
    in_stock: bool = False
    title: str = ""
    seller: str = ""
    image: str = ""            # URL de la foto principal
    variant: str = ""          # color / material / estilo elegido
    list_price: float | None = None   # el precio tachado ("List Price")
    discount_pct: float | None = None # el % de descuento que muestra Amazon
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.price is not None


class PriceProvider:
    name = "base"

    def fetch(self, asin: str, url: str) -> Quote:  # pragma: no cover
        raise NotImplementedError
