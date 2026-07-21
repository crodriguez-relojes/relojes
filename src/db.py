"""Capa de persistencia: SQLite + repositorio de links en CSV.

El CSV (data/watches.csv) es la FUENTE DE VERDAD de que se monitorea.
La base SQLite guarda el historico de precios y las alertas ya enviadas.
"""
from __future__ import annotations

import csv
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import DB_PATH, WATCHES_CSV

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    asin        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    url         TEXT NOT NULL,
    target_price REAL,
    category    TEXT,
    notes       TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    first_seen  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS price_history (
    asin        TEXT NOT NULL,
    day         TEXT NOT NULL,          -- YYYY-MM-DD
    price       REAL,                   -- NULL = no disponible / no leido
    currency    TEXT,
    in_stock    INTEGER,
    seller      TEXT,
    captured_at TEXT NOT NULL,
    PRIMARY KEY (asin, day)
);

CREATE INDEX IF NOT EXISTS idx_history_day ON price_history(day);

CREATE TABLE IF NOT EXISTS alerts_sent (
    asin    TEXT NOT NULL,
    rule    TEXT NOT NULL,
    day     TEXT NOT NULL,
    price   REAL,
    PRIMARY KEY (asin, rule, day)
);

CREATE TABLE IF NOT EXISTS runs (
    started_at TEXT PRIMARY KEY,
    ok_count   INTEGER,
    fail_count INTEGER,
    notes      TEXT
);
"""

ASIN_RE = re.compile(r"(?:/dp/|/gp/product/|/product/|asin=)([A-Z0-9]{10})", re.I)


@dataclass
class Product:
    asin: str
    name: str
    url: str
    target_price: float | None
    category: str
    notes: str
    active: bool


def extract_asin(url: str) -> str | None:
    m = ASIN_RE.search(url)
    return m.group(1).upper() if m else None


def canonical_url(asin: str, domain: str = "amazon.com") -> str:
    return f"https://www.{domain}/dp/{asin}"


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


# ---------------------------------------------------------------- watches.csv

def read_watches(path: Path = WATCHES_CSV) -> list[Product]:
    """Lee el repositorio de links. Tolera ASIN vacio (lo deduce del URL)."""
    products: list[Product] = []
    if not path.exists():
        return products
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            url = (row.get("url") or "").strip()
            if not url:
                continue
            asin = (row.get("asin") or "").strip().upper() or extract_asin(url)
            if not asin:
                print(f"  [!] Sin ASIN valido, se omite: {url[:70]}")
                continue
            raw_target = (row.get("target_price") or "").strip()
            products.append(
                Product(
                    asin=asin,
                    name=(row.get("name") or asin).strip(),
                    url=url,
                    target_price=float(raw_target) if raw_target else None,
                    category=(row.get("category") or "").strip(),
                    notes=(row.get("notes") or "").strip(),
                    active=(row.get("active") or "true").strip().lower()
                    not in ("false", "0", "no"),
                )
            )
    return products


def update_csv_names(names: dict[str, str], path: Path = WATCHES_CSV) -> None:
    """Escribe en watches.csv los nombres que se leyeron de Amazon.

    Asi el usuario puede pegar solo links: el archivo se completa solo en la
    primera corrida y queda legible para siempre.
    """
    if not names or not path.exists():
        return
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fields = reader.fieldnames or []
        rows = list(reader)
    changed = False
    for row in rows:
        asin = (row.get("asin") or "").strip().upper() or extract_asin(row.get("url", ""))
        if asin in names:
            row["name"] = names[asin]
            changed = True
    if not changed:
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def sync_products(conn: sqlite3.Connection, products: list[Product]) -> None:
    """Refleja watches.csv en la tabla products (upsert + desactivar ausentes)."""
    today = date.today().isoformat()
    seen = set()
    for p in products:
        seen.add(p.asin)
        conn.execute(
            """INSERT INTO products (asin,name,url,target_price,category,notes,active,first_seen)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(asin) DO UPDATE SET
                   name=excluded.name, url=excluded.url,
                   target_price=excluded.target_price, category=excluded.category,
                   notes=excluded.notes, active=excluded.active""",
            (p.asin, p.name, p.url, p.target_price, p.category, p.notes,
             int(p.active), today),
        )
    if seen:
        marks = ",".join("?" * len(seen))
        conn.execute(
            f"UPDATE products SET active=0 WHERE asin NOT IN ({marks})", tuple(seen)
        )
    conn.commit()


# ---------------------------------------------------------------- historico

def record_price(conn: sqlite3.Connection, asin: str, price: float | None,
                 currency: str, in_stock: bool, seller: str = "") -> None:
    conn.execute(
        """INSERT INTO price_history (asin,day,price,currency,in_stock,seller,captured_at)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(asin,day) DO UPDATE SET
               price=excluded.price, currency=excluded.currency,
               in_stock=excluded.in_stock, seller=excluded.seller,
               captured_at=excluded.captured_at""",
        (asin, date.today().isoformat(), price, currency, int(in_stock), seller,
         datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()


def history(conn: sqlite3.Connection, asin: str, days: int | None = None
            ) -> list[tuple[str, float]]:
    """Devuelve [(dia, precio)] ordenado ascendente, solo dias con precio."""
    sql = "SELECT day, price FROM price_history WHERE asin=? AND price IS NOT NULL"
    args: list = [asin]
    if days is not None:
        cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
        sql += " AND day >= ?"
        args.append(cutoff)
    sql += " ORDER BY day ASC"
    return [(r["day"], r["price"]) for r in conn.execute(sql, args)]


def already_alerted(conn: sqlite3.Connection, asin: str, rule: str,
                    cooldown_days: int) -> bool:
    cutoff = (date.today() - timedelta(days=cooldown_days)).isoformat()
    row = conn.execute(
        "SELECT 1 FROM alerts_sent WHERE asin=? AND rule=? AND day > ? LIMIT 1",
        (asin, rule, cutoff),
    ).fetchone()
    return row is not None


def mark_alerted(conn: sqlite3.Connection, asin: str, rule: str, price: float) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO alerts_sent (asin,rule,day,price) VALUES (?,?,?,?)",
        (asin, rule, date.today().isoformat(), price),
    )
    conn.commit()


def log_run(conn: sqlite3.Connection, ok: int, fail: int, notes: str = "") -> None:
    conn.execute(
        "INSERT OR REPLACE INTO runs (started_at,ok_count,fail_count,notes) VALUES (?,?,?,?)",
        (datetime.now().isoformat(timespec="seconds"), ok, fail, notes),
    )
    conn.commit()
