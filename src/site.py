"""Genera docs/data.js: el archivo que alimenta la pagina web del dashboard.

La pagina (docs/index.html) lee este archivo con una etiqueta <script>, no con
fetch(), a proposito: asi funciona igual abriendola con doble clic desde tu PC
que publicada en GitHub Pages.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from . import analyze as an
from .config import CFG, ROOT
from .db import history

DOCS_DIR = ROOT / "docs"


def _item(conn: sqlite3.Connection, a: an.Analysis) -> dict:
    return {
        "asin": a.asin,
        "name": a.name,
        "url": a.url,
        "category": "",
        "price": a.price,
        "prev_price": a.prev_price,
        "target_price": a.target_price,
        "min_7d": a.min_7d, "min_7d_date": a.min_7d_date,
        "min_30d": a.min_30d, "min_30d_date": a.min_30d_date,
        "min_all": a.min_all, "min_all_date": a.min_all_date,
        "max_all": a.max_all,
        "median_all": a.median_all,
        "pct_vs_prev": a.pct_vs_prev,
        "pct_vs_min_all": a.pct_vs_min_all,
        "discount_vs_typical": a.discount_vs_typical,
        "weekly_change": an.weekly_change(conn, a.asin),
        "volatility_pct": a.volatility_pct,
        "trend": an.trend(conn, a.asin, days=30),
        "best_weekday": an.best_weekday_to_buy(conn, a.asin),
        "history_days": a.history_days,
        "at_all_time_low": a.is_all_time_low,
        "triggered": a.triggered,
        "score": a.score,
        "recommendation": a.recommendation,
        # Ultimos 90 dias: suficiente para las graficas, mantiene el archivo liviano
        "history": [[d, p] for d, p in history(conn, a.asin)[-90:]],
    }


def build(conn: sqlite3.Connection, analyses: list[an.Analysis]) -> str:
    """Escribe docs/data.js y devuelve la ruta."""
    categories = {
        r["asin"]: r["category"] or ""
        for r in conn.execute("SELECT asin, category FROM products")
    }
    items = []
    for a in analyses:
        it = _item(conn, a)
        it["category"] = categories.get(a.asin, "")
        items.append(it)
    items.sort(key=lambda x: -(x["score"] or 0))

    days_tracked = conn.execute(
        "SELECT COUNT(DISTINCT day) FROM price_history"
    ).fetchone()[0]

    payload = {
        "demo": False,
        "generated_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "currency_symbol": CFG["marketplace"]["currency_symbol"],
        "days_tracked": days_tracked,
        "items": items,
    }

    DOCS_DIR.mkdir(exist_ok=True)
    out = DOCS_DIR / "data.js"
    out.write_text(
        "/* Generado automaticamente por: python -m src.main site — no editar a mano */\n"
        "window.RADAR_DATA = "
        + json.dumps(payload, ensure_ascii=False, indent=1)
        + ";\n",
        encoding="utf-8",
    )
    return str(out)
