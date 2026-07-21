"""CLI del sistema.

    python -m src.main track      # monitoreo diario + alertas   (cron diario)
    python -m src.main weekly     # reporte semanal              (cron lunes)
    python -m src.main monthly    # analisis mensual             (cron dia 1)
    python -m src.main site       # regenera la pagina web (docs/data.js)
    python -m src.main add URL    # agrega un link al repositorio
    python -m src.main list       # muestra el estado actual en consola
    python -m src.main export     # exporta el historico a CSV
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime

from tabulate import tabulate

from . import analyze as an
from .config import CFG, DATA_DIR, WATCHES_CSV
from .db import (already_alerted, canonical_url, connect, extract_asin, history,
                 log_run, mark_alerted, read_watches, record_price, sync_products,
                 update_csv_names)
from .notify import render, save_copy, send_email
from .providers import get_provider
from .site import build as build_site

SYMBOL = CFG["marketplace"]["currency_symbol"]


def _now() -> str:
    return datetime.now().strftime("%d/%m/%Y %H:%M")


def _analyses(conn, only_active: bool = True) -> list[an.Analysis]:
    """Analiza todos los productos usando el ultimo precio conocido.

    Sincroniza primero con watches.csv: asi un reloj agregado hoy ya aparece
    en el reporte de hoy, aunque todavia no tenga ni un precio registrado.
    """
    sync_products(conn, read_watches())
    sql = "SELECT * FROM products" + (" WHERE active=1" if only_active else "")
    out = []
    for row in conn.execute(sql):
        rows = history(conn, row["asin"])
        last_price = rows[-1][1] if rows else None
        out.append(an.analyze(conn, row["asin"], row["name"], row["url"],
                              last_price, row["target_price"]))
    return out


# --------------------------------------------------------------- comandos

def cmd_track(args) -> int:
    """Monitoreo diario: scrapea, guarda, analiza y alerta."""
    conn = connect()
    products = read_watches()
    sync_products(conn, products)
    active = [p for p in products if p.active]

    if not active:
        print("No hay productos activos en data/watches.csv")
        return 0

    provider = get_provider()
    print(f"Monitoreando {len(active)} relojes con proveedor '{provider.name}'\n")

    ok = fail = 0
    analyses: list[an.Analysis] = []
    discovered: dict[str, str] = {}   # nombres leidos de Amazon

    for i, p in enumerate(active, 1):
        print(f"[{i}/{len(active)}] {p.name[:50]}")
        quote = provider.fetch(p.asin, p.url)

        # Si pegaste solo el link, el nombre se completa solo la primera vez
        if quote.title and p.name == p.asin:
            p.name = quote.title[:90]
            discovered[p.asin] = p.name
            conn.execute("UPDATE products SET name=? WHERE asin=?", (p.name, p.asin))
            conn.commit()
            print(f"    nombre detectado: {p.name[:60]}")

        if quote.ok:
            record_price(conn, p.asin, quote.price, quote.currency,
                         quote.in_stock, quote.seller)
            ok += 1
            print(f"    {SYMBOL}{quote.price:,.2f}")
        else:
            record_price(conn, p.asin, None, quote.currency, False, "")
            fail += 1
            print(f"    [!] {quote.error}")

        a = an.analyze(conn, p.asin, p.name, p.url, quote.price,
                       p.target_price, quote.error)
        analyses.append(a)
        if a.triggered:
            print(f"    -> {a.recommendation} ({', '.join(a.triggered)})")

        if i < len(active) and hasattr(provider, "polite_pause"):
            provider.polite_pause()

    update_csv_names(discovered)
    log_run(conn, ok, fail)
    print(f"\nResultado: {ok} leidos, {fail} fallidos")

    # La pagina web se refresca en cada corrida diaria
    print(f"Dashboard actualizado: {build_site(conn, analyses)}")

    # ------------------------------------------------ alertas
    cooldown = CFG["alerts"]["cooldown_days"]
    to_alert = []
    for a in analyses:
        new_rules = [r for r in a.triggered
                     if not already_alerted(conn, a.asin, r, cooldown)]
        if not new_rules:
            continue
        a.reason = an.alert_reason(a)
        to_alert.append(a)
        for r in new_rules:
            mark_alerted(conn, a.asin, r, a.price)

    if not to_alert:
        print("Sin alertas nuevas hoy.")
        return 0

    to_alert.sort(key=lambda x: -x.score)
    subject = (f"{len(to_alert)} oportunidad(es) — "
               f"{to_alert[0].name[:40]} a {SYMBOL}{to_alert[0].price:,.2f}")
    html = render("alert.html", items=to_alert, generated_at=_now())
    save_copy(html, f"alerta-{date.today().isoformat()}.html")
    if not args.no_email:
        send_email(subject, html)
    return 0


def cmd_weekly(args) -> int:
    conn = connect()
    items = _analyses(conn)
    for a in items:
        a.weekly_change = an.weekly_change(conn, a.asin)

    priced = [a for a in items if a.price is not None]
    moved = [a for a in priced if a.weekly_change is not None]
    top_n = CFG["reports"]["top_opportunities"]

    html = render(
        "weekly.html",
        generated_at=_now(),
        week_label=f"semana {date.today().isocalendar()[1]} de {date.today().year}",
        total=len(items),
        buy_now=sum(1 for a in priced if a.recommendation == "COMPRAR AHORA"),
        at_low=sum(1 for a in priced if a.is_all_time_low),
        top=sorted(priced, key=lambda x: -x.score)[:top_n],
        droppers=sorted([a for a in moved if a.weekly_change < 0],
                        key=lambda x: x.weekly_change)[:5],
        risers=sorted([a for a in moved if a.weekly_change > 0],
                      key=lambda x: -x.weekly_change)[:5],
        all_items=sorted(items, key=lambda x: x.name.lower()),
    )
    path = save_copy(html, f"semanal-{date.today().isoformat()}.html")
    print(f"Reporte guardado en {path}")
    if not args.no_email:
        send_email(f"Reporte semanal — {len(items)} relojes", html)
    return 0


def cmd_monthly(args) -> int:
    conn = connect()
    items = _analyses(conn)
    for a in items:
        a.trend = an.trend(conn, a.asin, days=30)
        a.best_weekday = an.best_weekday_to_buy(conn, a.asin)
        a.reason = ""

    priced = [a for a in items if a.price is not None]

    stock_up = []
    for a in sorted(priced, key=lambda x: -x.score):
        d = a.discount_vs_typical
        if a.score >= CFG["recommendation"]["buy_now_score"] or (d and d >= 15):
            a.reason = (f"{d:.0f}% bajo su precio habitual" if d
                        else f"score {a.score}")
            stock_up.append(a)
    stock_up = stock_up[:6]

    avoid = []
    for a in priced:
        if a.pct_vs_min_all is not None and a.pct_vs_min_all > 25:
            a.reason = f"{a.pct_vs_min_all:.0f}% sobre su minimo historico"
            avoid.append(a)
    avoid = sorted(avoid, key=lambda x: -(x.pct_vs_min_all or 0))[:6]

    days_tracked = conn.execute(
        "SELECT COUNT(DISTINCT day) FROM price_history").fetchone()[0]
    portfolio_avg = (sum(a.price for a in priced) / len(priced)) if priced else None

    html = render(
        "monthly.html",
        generated_at=_now(),
        month_label=date.today().strftime("%B %Y"),
        total=len(items),
        days_tracked=days_tracked,
        buy_now=sum(1 for a in priced if a.recommendation == "COMPRAR AHORA"),
        portfolio_avg=portfolio_avg,
        all_items=sorted(items, key=lambda x: x.name.lower()),
        stock_up=stock_up,
        avoid=avoid,
        volatile=sorted(priced, key=lambda x: -x.volatility_pct)[:6],
    )
    path = save_copy(html, f"mensual-{date.today().isoformat()}.html")
    print(f"Reporte guardado en {path}")
    if not args.no_email:
        send_email(f"Analisis mensual — {date.today().strftime('%B %Y')}", html)
    return 0


def cmd_site(args) -> int:
    """Regenera la pagina web sin volver a consultar Amazon."""
    conn = connect()
    path = build_site(conn, _analyses(conn))
    print(f"Pagina actualizada: {path}")
    print("Abrela con doble clic en docs/index.html")
    return 0


def cmd_add(args) -> int:
    """Agrega un link al repositorio, leyendo el nombre desde Amazon."""
    asin = extract_asin(args.url)
    if not asin:
        print("No pude extraer el ASIN de ese URL.")
        return 1
    existing = {p.asin for p in read_watches()}
    if asin in existing:
        print(f"{asin} ya esta en el repositorio.")
        return 0

    name = args.name
    if not name:
        print("Leyendo nombre desde Amazon...")
        q = get_provider().fetch(asin, canonical_url(asin, CFG["marketplace"]["domain"]))
        name = (q.title or asin)[:90]

    with open(WATCHES_CSV, "a", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow([
            asin, name, canonical_url(asin, CFG["marketplace"]["domain"]),
            args.target or "", "true", args.category or "", ""])
    print(f"Agregado: {name}  ({asin})")
    return 0


def cmd_list(args) -> int:
    conn = connect()
    items = _analyses(conn, only_active=not args.all)
    rows = []
    for a in sorted(items, key=lambda x: -x.score):
        rows.append([
            a.name[:38],
            f"{SYMBOL}{a.price:,.2f}" if a.price else "-",
            f"{SYMBOL}{a.min_7d:,.2f}" if a.min_7d else "-",
            f"{SYMBOL}{a.min_all:,.2f}" if a.min_all else "-",
            f"{a.pct_vs_min_all:+.1f}%" if a.pct_vs_min_all is not None else "-",
            a.score, a.recommendation, a.history_days,
        ])
    print(tabulate(rows, headers=["Reloj", "Actual", "Min 7d", "Min hist.",
                                  "vs min", "Score", "Recomendacion", "Dias"],
                   tablefmt="simple"))
    return 0


def cmd_export(args) -> int:
    conn = connect()
    out = DATA_DIR / "historico_precios.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["asin", "nombre", "dia", "precio", "moneda", "en_stock"])
        for r in conn.execute(
            """SELECT h.asin, p.name, h.day, h.price, h.currency, h.in_stock
               FROM price_history h LEFT JOIN products p ON p.asin=h.asin
               ORDER BY h.asin, h.day"""
        ):
            w.writerow(list(r))
    print(f"Exportado a {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="relojes", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name, fn in (("track", cmd_track), ("weekly", cmd_weekly),
                     ("monthly", cmd_monthly)):
        s = sub.add_parser(name)
        s.add_argument("--no-email", action="store_true",
                       help="genera el HTML pero no envia correo")
        s.set_defaults(func=fn)

    s = sub.add_parser("site")
    s.set_defaults(func=cmd_site)

    s = sub.add_parser("add")
    s.add_argument("url")
    s.add_argument("--name")
    s.add_argument("--target", type=float, help="precio objetivo para alertas")
    s.add_argument("--category")
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("list")
    s.add_argument("--all", action="store_true", help="incluye inactivos")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("export")
    s.set_defaults(func=cmd_export)
    return ap


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
