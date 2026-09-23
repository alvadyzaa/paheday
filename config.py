"""Baca konfigurasi dari .env / environment."""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _csv(name: str) -> list[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return []
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, str(default)).strip().lower()
    return raw in ("1", "true", "yes", "y", "on")


class Config:
    BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    CHAT_IDS: list[str] = _csv("TELEGRAM_CHAT_IDS")

    CHECK_INTERVAL: int = int(os.getenv("CHECK_INTERVAL_SECONDS", "600"))
    PER_PAGE: int = min(int(os.getenv("PER_PAGE", "15")), 100)
    STATE_FILE: str = os.getenv("STATE_FILE", "state.json")

    NOTIFY_UPDATES: bool = _bool("NOTIFY_UPDATES", True)
    SEND_STARTUP_MESSAGE: bool = _bool("SEND_STARTUP_MESSAGE", False)

    PAHE_INCLUDE: list[str] = _csv("PAHE_INCLUDE")
    PAHE_EXCLUDE: list[str] = _csv("PAHE_EXCLUDE")
    DRAMADAY_INCLUDE: list[str] = _csv("DRAMADAY_INCLUDE")
    DRAMADAY_EXCLUDE: list[str] = _csv("DRAMADAY_EXCLUDE")

    PAHE_URL = "https://pahe.ink"
    DRAMADAY_URL = "https://dramaday.me"

    @classmethod
    def validate(cls, strict: bool = True) -> list[str]:
        errors: list[str] = []
        if not cls.BOT_TOKEN:
            errors.append("TELEGRAM_BOT_TOKEN kosong (isi di .env)")
        if not cls.CHAT_IDS:
            errors.append("TELEGRAM_CHAT_IDS kosong (isi di .env)")
        if strict and errors:
            raise SystemExit("Config error:\n- " + "\n- ".join(errors))
        return errors
