"""Carga de configuracion y rutas del proyecto."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
WATCHES_CSV = DATA_DIR / "watches.csv"
DB_PATH = DATA_DIR / "prices.db"
CONFIG_PATH = ROOT / "config.yaml"

load_dotenv(ROOT / ".env")


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


CFG = load_config()
