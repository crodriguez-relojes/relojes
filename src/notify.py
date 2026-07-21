"""Envio de correos por Gmail SMTP con plantillas HTML."""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import CFG, ROOT, env

TEMPLATES_DIR = ROOT / "templates"

_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html"]),
)
_env.filters["money"] = lambda v: (
    "-" if v is None else f"{CFG['marketplace']['currency_symbol']}{v:,.2f}"
)
_env.filters["pct"] = lambda v: "-" if v is None else f"{v:+.1f}%"


def render(template_name: str, **ctx) -> str:
    ctx.setdefault("symbol", CFG["marketplace"]["currency_symbol"])
    return _env.get_template(template_name).render(**ctx)


def send_email(subject: str, html: str, text_fallback: str = "") -> bool:
    """Envia por SMTP de Gmail. Requiere GMAIL_USER y GMAIL_APP_PASSWORD."""
    if not CFG["email"]["enabled"]:
        print("  [i] Email deshabilitado en config.yaml")
        return False

    # SMTP_* es el nombre nuevo (sirve para Gmail, Brevo, Resend, el que sea).
    # GMAIL_* se sigue aceptando para no romper configuraciones viejas.
    user = env("SMTP_USER") or env("GMAIL_USER")
    password = env("SMTP_PASSWORD") or env("GMAIL_APP_PASSWORD")
    to_addr = env("ALERT_TO") or user
    # Algunos proveedores autentican con un usuario distinto al remitente
    from_addr = env("MAIL_FROM") or user
    if not user or not password:
        print("  [!] Faltan SMTP_USER / SMTP_PASSWORD: no se envia correo")
        return False

    msg = EmailMessage()
    msg["Subject"] = f"{CFG['email']['subject_prefix']} {subject}"
    msg["From"] = f"Radar de Relojes <{from_addr}>"
    msg["To"] = to_addr
    msg.set_content(text_fallback or "Este correo requiere un cliente con HTML.")
    msg.add_alternative(html, subtype="html")

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(CFG["email"]["smtp_host"], CFG["email"]["smtp_port"],
                          timeout=30) as server:
            server.starttls(context=ctx)
            server.login(user, password)
            server.send_message(msg)
        print(f"  [ok] Correo enviado a {to_addr}: {subject}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  [!] Error enviando correo: {exc}")
        return False


def save_copy(html: str, filename: str) -> Path:
    """Guarda una copia local del reporte (util para revisar sin correo)."""
    out_dir = ROOT / "reports"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / filename
    path.write_text(html, encoding="utf-8")
    return path
