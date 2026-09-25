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


def _bool_opt(name: str) -> bool | None:
    """Return True/False kalau env di-set, None kalau kosong (untuk fallback)."""
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return None
    return raw in ("1", "true", "yes", "y", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except (ValueError, AttributeError):
        return default


def _int_opt(name: str) -> int | None:
    """Return int kalau env di-set valid, None kalau kosong/invalid."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


class Config:
    BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    CHAT_IDS: list[str] = _csv("TELEGRAM_CHAT_IDS")

    CHECK_INTERVAL: int = int(os.getenv("CHECK_INTERVAL_SECONDS", "600"))
    PER_PAGE: int = min(int(os.getenv("PER_PAGE", "15")), 100)
    STATE_FILE: str = os.getenv("STATE_FILE", "state.json")

    NOTIFY_UPDATES: bool = _bool("NOTIFY_UPDATES", True)
    SEND_STARTUP_MESSAGE: bool = _bool("SEND_STARTUP_MESSAGE", False)

    # --- Anti-spam postingan lama (mis. 2018 ke-touch modified-nya) ---
    # Hanya postingan dengan umur publish <= batas yang boleh memicu notif.
    # 0 = tanpa batas (perilaku lama). Disarankan tetap dibatasi.
    MAX_AGE_DAYS: int = _int("MAX_AGE_DAYS", 30)
    _PAHE_MAX_AGE_OPT: int | None = _int_opt("PAHE_MAX_AGE_DAYS")
    _DRAMADAY_MAX_AGE_OPT: int | None = _int_opt("DRAMADAY_MAX_AGE_DAYS")
    # Pahe posting deras (puluhan/hari) dan pack Complete sering ke-touch ulang
    # tanpa perubahan isi -> batas 3 hari agar hanya rilisan fresh yang notif.
    # Dramaday boleh 60-90 hari karena drama ongoing update episode 2-3 bulan
    # setelah publish.
    PAHE_MAX_AGE_DAYS: int = _PAHE_MAX_AGE_OPT if _PAHE_MAX_AGE_OPT is not None else 3
    DRAMADAY_MAX_AGE_DAYS: int = (
        _DRAMADAY_MAX_AGE_OPT if _DRAMADAY_MAX_AGE_OPT is not None else 90
    )

    # --- Per-source update flag ---
    # Pahe = film sekali-post, update hampir selalu noise (sentuhan WP/iklan/SEO)
    # -> default OFF. Dramaday update = episode baru -> ikut global NOTIFY_UPDATES.
    _PAHE_UPD_OPT: bool | None = _bool_opt("PAHE_NOTIFY_UPDATES")
    _DRAMADAY_UPD_OPT: bool | None = _bool_opt("DRAMADAY_NOTIFY_UPDATES")
    PAHE_NOTIFY_UPDATES: bool = (
        _PAHE_UPD_OPT if _PAHE_UPD_OPT is not None else False
    )
    DRAMADAY_NOTIFY_UPDATES: bool = (
        _DRAMADAY_UPD_OPT if _DRAMADAY_UPD_OPT is not None else NOTIFY_UPDATES
    )

    PAHE_INCLUDE: list[str] = _csv("PAHE_INCLUDE")
    PAHE_EXCLUDE: list[str] = _csv("PAHE_EXCLUDE")
    DRAMADAY_INCLUDE: list[str] = _csv("DRAMADAY_INCLUDE")
    DRAMADAY_EXCLUDE: list[str] = _csv("DRAMADAY_EXCLUDE")
    N3X_INCLUDE: list[str] = _csv("N3X_INCLUDE")
    N3X_EXCLUDE: list[str] = _csv("N3X_EXCLUDE")

    # --- n3x.me: API tanpa modified -> hanya postingan BARU yang fire ---
    _N3X_MAX_AGE_OPT: int | None = _int_opt("N3X_MAX_AGE_DAYS")
    N3X_MAX_AGE_DAYS: int = (
        _N3X_MAX_AGE_OPT if _N3X_MAX_AGE_OPT is not None else 14
    )
    _N3X_UPD_OPT: bool | None = _bool_opt("N3X_NOTIFY_UPDATES")
    N3X_NOTIFY_UPDATES: bool = (
        _N3X_UPD_OPT if _N3X_UPD_OPT is not None else False
    )

    PAHE_URL = "https://pahe.ink"
    DRAMADAY_URL = "https://dramaday.me"
    N3X_URL = "https://n3x.me"

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
