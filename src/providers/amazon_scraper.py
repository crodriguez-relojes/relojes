"""Scraper de Amazon con multiples selectores de respaldo y anti-bloqueo basico.

Amazon cambia su HTML seguido: por eso se prueban varios selectores en cascada
y, si todos fallan, se cae a una expresion regular sobre el HTML crudo.
"""
from __future__ import annotations

import random
import re
import time

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import CFG
from .base import PriceProvider, Quote

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

# Selectores ordenados de mas fiable a menos.
PRICE_SELECTORS = [
    "#corePriceDisplay_desktop_feature_div span.a-price span.a-offscreen",
    "#corePrice_feature_div span.a-price span.a-offscreen",
    "#corePrice_desktop span.a-price span.a-offscreen",
    "#snsPrice span.a-price span.a-offscreen",
    "#priceblock_ourprice",
    "#priceblock_dealprice",
    "#price_inside_buybox",
    "span.priceToPay span.a-offscreen",
    "span.apexPriceToPay span.a-offscreen",
    "div#buybox span.a-price span.a-offscreen",
]

TITLE_SELECTORS = ["#productTitle", "span#productTitle", "h1#title"]

PRICE_RE = re.compile(r"[\d][\d,\.]*")
FALLBACK_RE = re.compile(r'"priceAmount"\s*:\s*([0-9]+\.?[0-9]*)')

# La foto principal. Amazon la publica en varios sitios; se prueban en orden.
IMAGE_SELECTORS = [
    "#landingImage",
    "#imgBlkFront",
    "#main-image",
    "#imgTagWrapperId img",
    "div#imageBlock img",
]
# Filas de variante: color, material de la correa, estilo...
VARIANT_SELECTORS = [
    "#variation_color_name .selection",
    "#variation_style_name .selection",
    "#variation_band_material_type .selection",
    "#variation_size_name .selection",
    "#twister .selection",
]
# Tabla "Product overview" que Amazon muestra bajo el titulo
OVERVIEW_KEYS = ("color", "band color", "band material", "case material",
                 "dial color", "material", "style", "strap color")
# Token de tamano dentro del URL de imagen de Amazon (._AC_SX679_.jpg)
IMG_SIZE_RE = re.compile(r"\._[A-Z0-9_,]+_\.(jpg|png|webp)$", re.I)

# Amazon declara la moneda del precio en el JSON incrustado de la pagina
CURRENCY_RE = re.compile(r'"currencyCode"\s*:\s*"([A-Z]{3})"')


def thumb(url: str, px: int = 320) -> str:
    """Pide a Amazon una version mas liviana de la misma foto."""
    if not url:
        return ""
    return IMG_SIZE_RE.sub(rf"._AC_SX{px}_.\1", url)

BLOCK_MARKERS = (
    "api-services-support@amazon.com",
    "Type the characters you see in this image",
    "Enter the characters you see below",
    "Robot Check",
)


def _parse_price(text: str) -> float | None:
    if not text:
        return None
    text = text.replace(" ", " ").strip()
    m = PRICE_RE.search(text)
    if not m:
        return None
    raw = m.group(0)
    # Formato US: 1,234.56  -> la coma es separador de miles
    raw = raw.replace(",", "")
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


class AmazonScraper(PriceProvider):
    name = "amazon_scraper"

    def __init__(self) -> None:
        self.cfg = CFG["scraping"]
        self.market = CFG["marketplace"]
        self.session = requests.Session()
        self.bloqueos = 0        # cuantas veces Amazon nos freno en esta corrida
        # Amazon decide la moneda por la IP: desde Colombia sirve COP y desde
        # un datacenter de EEUU sirve USD. Mezclar monedas en el historial lo
        # arruina, asi que se fija la preferencia por cookie.
        self.session.cookies.set("i18n-prefs", self.market["currency"],
                                 domain=".amazon.com")
        self.session.cookies.set("lc-main", "en_US", domain=".amazon.com")

    def _headers(self) -> dict:
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": self.market["accept_language"],
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Cache-Control": "no-cache",
        }

    @retry(stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=8, min=8, max=120),
           reraise=True)
    def _get(self, url: str) -> str:
        resp = self.session.get(
            url, headers=self._headers(), timeout=self.cfg["timeout_seconds"]
        )
        if resp.status_code in (429, 503):
            # Amazon esta frenando: esperar de verdad antes de que tenacity
            # vuelva a intentar, o los reintentos solo empeoran el bloqueo.
            self.bloqueos += 1
            time.sleep(self.cfg.get("cooldown_on_block_seconds", 90))
            raise RuntimeError(f"Amazon respondio {resp.status_code} (rate limit)")
        resp.raise_for_status()
        return resp.text

    def fetch(self, asin: str, url: str) -> Quote:
        currency = self.market["currency"]
        try:
            html = self._get(url)
        except Exception as exc:  # noqa: BLE001
            return Quote(asin=asin, price=None, currency=currency,
                         error=f"red: {exc}")

        if any(marker in html for marker in BLOCK_MARKERS):
            # Seguir consultando al mismo ritmo solo profundiza el bloqueo
            self.bloqueos += 1
            time.sleep(self.cfg.get("cooldown_on_block_seconds", 90))
            return Quote(asin=asin, price=None, currency=currency,
                         error="bloqueado por CAPTCHA de Amazon")

        soup = BeautifulSoup(html, "lxml")

        title = ""
        for sel in TITLE_SELECTORS:
            node = soup.select_one(sel)
            if node and node.get_text(strip=True):
                title = node.get_text(strip=True)
                break

        # --- foto principal -------------------------------------------
        image = ""
        for sel in IMAGE_SELECTORS:
            node = soup.select_one(sel)
            if not node:
                continue
            # data-old-hires trae la version grande; si no, la primera del set
            image = node.get("data-old-hires") or node.get("src") or ""
            if not image and node.get("data-a-dynamic-image"):
                m = re.search(r'"(https://[^"]+)"', node["data-a-dynamic-image"])
                image = m.group(1) if m else ""
            if image.startswith("http"):
                break
            image = ""
        if not image:
            og = soup.select_one('meta[property="og:image"]')
            if og and og.get("content", "").startswith("http"):
                image = og["content"]
        image = thumb(image)

        # --- variante (lo que diferencia dos ASIN del mismo modelo) ----
        partes = []
        for sel in VARIANT_SELECTORS:
            node = soup.select_one(sel)
            if node:
                txt = node.get_text(" ", strip=True)
                if txt and txt.lower() not in ("select", "seleccionar"):
                    partes.append(txt)
        # Plan B: la tabla "Product overview" (Color, Band Material...)
        if not partes:
            for row in soup.select("#productOverview_feature_div tr"):
                celdas = row.find_all(["td", "th"])
                if len(celdas) < 2:
                    continue
                clave = celdas[0].get_text(" ", strip=True).lower()
                valor = celdas[1].get_text(" ", strip=True)
                if any(k in clave for k in OVERVIEW_KEYS) and valor:
                    partes.append(valor)

        # Plan C: el "twister" que Amazon incrusta como JSON en la pagina.
        # Mapea cada ASIN hermano con los valores que lo distinguen:
        #   "B017SN1OI8":["Brown","Leather"]
        if not partes:
            m = re.search(rf'"{re.escape(asin)}"\s*:\s*\[([^\]]*)\]', html)
            if m:
                partes = [v.strip(' "') for v in m.group(1).split(",")
                          if v.strip(' "') and len(v.strip(' "')) < 40]

        variant = " / ".join(dict.fromkeys(partes))[:70]

        price = None
        for sel in PRICE_SELECTORS:
            node = soup.select_one(sel)
            if node:
                price = _parse_price(node.get_text())
                if price:
                    break
        if price is None:
            m = FALLBACK_RE.search(html)
            if m:
                price = _parse_price(m.group(1))

        availability = ""
        avail_node = soup.select_one("#availability")
        if avail_node:
            availability = avail_node.get_text(" ", strip=True).lower()
        out_of_stock = any(
            s in availability for s in ("unavailable", "out of stock", "no disponible")
        )

        seller = ""
        seller_node = soup.select_one("#sellerProfileTriggerId, #merchant-info")
        if seller_node:
            seller = seller_node.get_text(" ", strip=True)[:80]

        if price is None:
            return Quote(asin=asin, price=None, currency=currency, title=title,
                         image=image, variant=variant,
                         error="sin stock" if out_of_stock else "precio no encontrado")

        # Guardar un precio en otra moneda corrompe el historial entero: los
        # minimos y los porcentajes se calculan sobre una serie que asume una
        # sola unidad. Ante la duda, mejor no guardar nada.
        m = CURRENCY_RE.search(html)
        if m and m.group(1) != currency:
            return Quote(asin=asin, price=None, currency=m.group(1), title=title,
                         image=image, variant=variant,
                         error=f"Amazon respondio en {m.group(1)}, se esperaba "
                               f"{currency}: precio descartado")

        return Quote(asin=asin, price=price, currency=currency,
                     in_stock=not out_of_stock, title=title, seller=seller,
                     image=image, variant=variant)

    def polite_pause(self) -> None:
        time.sleep(random.uniform(self.cfg["delay_min_seconds"],
                                  self.cfg["delay_max_seconds"]))
