"""Motor de analisis: minimos, variaciones, reglas de alerta y recomendacion."""
from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .config import CFG
from .db import history

RULE_LABELS = {
    "min_all_time": "MINIMO HISTORICO",
    "min_30d": "Minimo de 30 dias",
    "min_7d": "Minimo de 7 dias",
    "target_price": "Bajo tu precio objetivo",
    "daily_drop": "Caida fuerte en 24h",
}

# Peso de cada regla en el puntaje de recomendacion (0-100)
RULE_WEIGHTS = {
    "min_all_time": 45,
    "min_30d": 25,
    "min_7d": 12,
    "target_price": 30,
    "daily_drop": 15,
}


@dataclass
class Analysis:
    asin: str
    name: str
    url: str
    price: float | None
    prev_price: float | None = None
    min_7d: float | None = None
    min_7d_date: str = ""
    min_30d: float | None = None
    min_30d_date: str = ""
    min_all: float | None = None
    min_all_date: str = ""
    max_all: float | None = None
    avg_30d: float | None = None
    median_all: float | None = None
    target_price: float | None = None
    history_days: int = 0
    volatility_pct: float = 0.0
    triggered: list[str] = field(default_factory=list)
    score: int = 0
    recommendation: str = "MONITOREAR"
    error: str = ""

    # ---- porcentajes utiles para los reportes -------------------------
    @property
    def pct_vs_prev(self) -> float | None:
        if self.price is None or not self.prev_price:
            return None
        return (self.price - self.prev_price) / self.prev_price * 100

    @property
    def pct_vs_min_all(self) -> float | None:
        if self.price is None or not self.min_all:
            return None
        return (self.price - self.min_all) / self.min_all * 100

    @property
    def discount_vs_typical(self) -> float | None:
        """Descuento frente al precio habitual (mediana historica)."""
        if self.price is None or not self.median_all:
            return None
        return (self.median_all - self.price) / self.median_all * 100

    @property
    def is_all_time_low(self) -> bool:
        return "min_all_time" in self.triggered


def _min_with_date(rows: list[tuple[str, float]]) -> tuple[float | None, str]:
    if not rows:
        return None, ""
    day, price = min(rows, key=lambda r: r[1])
    return price, day


def analyze(conn: sqlite3.Connection, asin: str, name: str, url: str,
            price: float | None, target_price: float | None,
            error: str = "") -> Analysis:
    """Calcula todas las metricas y decide que reglas se disparan."""
    acfg = CFG["alerts"]
    rcfg = CFG["recommendation"]
    tol = 1 + acfg["tie_tolerance_pct"] / 100

    all_rows = history(conn, asin)
    a = Analysis(asin=asin, name=name, url=url, price=price,
                 target_price=target_price, error=error)
    a.history_days = len(all_rows)

    if all_rows:
        prices_all = [p for _, p in all_rows]
        a.min_all, a.min_all_date = _min_with_date(all_rows)
        a.max_all = max(prices_all)
        a.median_all = statistics.median(prices_all)
        # El precio de "ayer" = ultimo registro distinto del de hoy
        today = date.today().isoformat()
        previous = [r for r in all_rows if r[0] != today]
        if previous:
            a.prev_price = previous[-1][1]
        if len(prices_all) >= 3:
            mean = statistics.mean(prices_all)
            if mean:
                a.volatility_pct = statistics.pstdev(prices_all) / mean * 100

    rows_7 = history(conn, asin, days=7)
    rows_30 = history(conn, asin, days=30)
    a.min_7d, a.min_7d_date = _min_with_date(rows_7)
    a.min_30d, a.min_30d_date = _min_with_date(rows_30)
    if rows_30:
        a.avg_30d = statistics.mean([p for _, p in rows_30])

    if price is None:
        a.recommendation = "REVISAR MANUALMENTE"
        return a

    # ------------------------------------------------ reglas de alerta
    changed_enough = (
        a.prev_price is None
        or abs(price - a.prev_price) >= acfg["min_abs_change_usd"]
    )

    if acfg["min_7d"] and a.min_7d and price <= a.min_7d * tol and len(rows_7) >= 3:
        a.triggered.append("min_7d")
    if acfg["min_30d"] and a.min_30d and price <= a.min_30d * tol and len(rows_30) >= 7:
        a.triggered.append("min_30d")
    if (acfg["min_all_time"] and a.min_all and price <= a.min_all * tol
            and a.history_days >= rcfg["min_history_days_for_alltime"]):
        a.triggered.append("min_all_time")
    if (acfg["daily_drop"] and a.pct_vs_prev is not None and changed_enough
            and a.pct_vs_prev <= -acfg["daily_drop_pct"]):
        a.triggered.append("daily_drop")
    if acfg["target_price"] and target_price and price <= target_price:
        a.triggered.append("target_price")

    # ------------------------------------------------ puntaje 0-100
    score = sum(RULE_WEIGHTS.get(r, 0) for r in a.triggered)
    # Bonus por que tan cerca esta del minimo historico
    if a.pct_vs_min_all is not None:
        if a.pct_vs_min_all <= 2:
            score += 15
        elif a.pct_vs_min_all <= 8:
            score += 8
    # Bonus por descuento vs precio habitual
    d = a.discount_vs_typical
    if d is not None:
        if d >= 20:
            score += 15
        elif d >= 10:
            score += 8
    # Penalizacion: poca historia = poca confianza
    if a.history_days < 7:
        score = int(score * 0.6)
    a.score = max(0, min(100, score))

    if a.score >= rcfg["buy_now_score"]:
        a.recommendation = "COMPRAR AHORA"
    elif a.score >= rcfg["monitor_score"]:
        a.recommendation = "MONITOREAR"
    else:
        a.recommendation = "ESPERAR"
    return a


def alert_reason(a: Analysis) -> str:
    """Texto legible del motivo principal de la alerta."""
    for rule in ("min_all_time", "target_price", "min_30d", "daily_drop", "min_7d"):
        if rule in a.triggered:
            return RULE_LABELS[rule]
    return "Movimiento de precio"


def weekly_change(conn: sqlite3.Connection, asin: str) -> float | None:
    """Variacion % entre el precio de hace ~7 dias y el actual."""
    rows = history(conn, asin, days=8)
    if len(rows) < 2:
        return None
    first, last = rows[0][1], rows[-1][1]
    return (last - first) / first * 100 if first else None


def trend(conn: sqlite3.Connection, asin: str, days: int = 30) -> str:
    """Tendencia simple por regresion lineal sobre el periodo."""
    rows = history(conn, asin, days=days)
    if len(rows) < 5:
        return "sin datos suficientes"
    xs = list(range(len(rows)))
    ys = [p for _, p in rows]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return "estable"
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
    pct_per_week = slope * 7 / my * 100 if my else 0
    if pct_per_week <= -1.5:
        return f"bajando ({pct_per_week:.1f}%/sem)"
    if pct_per_week >= 1.5:
        return f"subiendo (+{pct_per_week:.1f}%/sem)"
    return "estable"


def best_weekday_to_buy(conn: sqlite3.Connection, asin: str) -> str:
    """Dia de la semana en que historicamente el precio esta mas bajo."""
    rows = history(conn, asin)
    if len(rows) < 21:
        return "-"
    buckets: dict[int, list[float]] = {}
    for day, price in rows:
        wd = datetime.strptime(day, "%Y-%m-%d").weekday()
        buckets.setdefault(wd, []).append(price)
    names = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]
    avgs = {wd: statistics.mean(v) for wd, v in buckets.items() if len(v) >= 2}
    if not avgs:
        return "-"
    return names[min(avgs, key=avgs.get)]
