"""Slash-command handler Telegram (getUpdates drain, tanpa server/webhook).

Perintah yang didukung:
    /start, /help            -> bantuan
    /upcoming_kdrama         -> jadwal tayang Korea (TVMaze, hari ini + besok)
    /upcoming_series         -> jadwal tayang US (TVMaze, hari ini + besok)
    /upcoming_movies         -> film segera rilis (TMDB, butuh TMDB_API_KEY)

Cara kerja: tiap run (cron Actions maupun loop) ambil update Telegram yang
belum dibaca (offset tersimpan di state.json), balas perintah, simpan offset.
Konsekuensi di Actions: balasan bisa telat sampai sela cron (~30 menit).
Hanya chat yang terdaftar di TELEGRAM_CHAT_IDS yang dilayani.
"""
from __future__ import annotations

import html
import time
from datetime import date, timedelta

import requests

from config import Config
from notify import send_message
from storage import load, save

TVMAZE_SCHEDULE = "https://api.tvmaze.com/schedule"
TG_API = "https://api.telegram.org/bot{token}/{method}"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

# Tipe acara yang dibuang (noise berita/olahraga/talkshow)
SKIP_TYPES = {"news", "talk show", "sports"}

_HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
_BULAN = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "Mei", 6: "Jun",
    7: "Jul", 8: "Agu", 9: "Sep", 10: "Okt", 11: "Nov", 12: "Des",
}

HELP_TEXT = (
    "<b>PaheDay commands</b>\n"
    "/upcoming_kdrama - jadwal tayang Korea (hari ini + besok)\n"
    "/upcoming_series - jadwal tayang US (hari ini + besok)\n"
    "/upcoming_movies - film segera rilis (TMDB)\n"
    "/help - pesan ini"
)


def fetch_schedule(country: str, day: date) -> list[dict]:
    """Jadwal tayang TVMaze untuk satu negara + tanggal. Raises kalau gagal."""
    r = requests.get(
        TVMAZE_SCHEDULE,
        params={"country": country.upper(), "date": day.isoformat()},
        headers={"User-Agent": UA},
        timeout=25,
    )
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def _show_line(item: dict) -> str | None:
    """Satu baris 'Show S1E2 - Network 19:40'. None kalau tipe di-skip."""
    show = item.get("show", {}) or {}
    if str(show.get("type") or "").lower() in SKIP_TYPES:
        return None
    name = (show.get("name") or "?").strip()
    net = ((show.get("network") or {}).get("name")
           or (show.get("webChannel") or {}).get("name") or "").strip()
    se, ep = item.get("season"), item.get("number")
    ep_tag = f" S{se}E{ep}" if se and ep else ""
    jam = str(item.get("airtime") or "")[:5]
    ekor = f"{net} {jam}".strip()
    # judul bisa diklik: halaman TVMaze (selalu ada) atau situs resmi
    url = (show.get("url") or show.get("officialSite") or "").strip()
    judul = html.escape(name)
    if url:
        judul = f'<a href="{html.escape(url, quote=True)}">{judul}</a>'
    return f"{judul}{ep_tag} - {ekor}" if ekor else f"{judul}{ep_tag}"


def format_upcoming(country: str, label: str, days: int = 2, limit: int = 30) -> str:
    """Teks jadwal TVMaze hari ini + besok. Aman <=4000 char."""
    blocks = [f"<b>{label} (sumber: TVMaze)</b>"]
    for i in range(max(days, 1)):
        d = date.today() + timedelta(days=i)
        try:
            items = fetch_schedule(country, d)
        except Exception as e:  # noqa: BLE001
            return f"Gagal ambil jadwal {label}: {e}"
        lines: list[str] = []
        for it in items:
            line = _show_line(it)
            if line:
                lines.append(line)
            if len(lines) >= limit:
                break
        hari = f"{_HARI[d.weekday()]}, {d.day} {_BULAN[d.month]}"
        blocks.append(f"\n<b>{hari}</b> ({len(lines)} tayangan)")
        if lines:
            blocks.extend(f"{n + 1}. {ln}" for n, ln in enumerate(lines))
        else:
            blocks.append("(tidak ada)")
    return "\n".join(blocks)[:4000]


TMDB_API = "https://api.themoviedb.org/3"


def fetch_upcoming_movies(region: str = "ID", page: int = 1) -> list[dict]:
    """Film segera rilis via TMDB. Butuh Config.TMDB_API_KEY. Raises kalau gagal."""
    if not Config.TMDB_API_KEY:
        raise RuntimeError("TMDB_API_KEY belum dipasang (isi di .env / secrets)")
    r = requests.get(
        f"{TMDB_API}/movie/upcoming",
        params={"api_key": Config.TMDB_API_KEY, "region": (region or "ID").upper(),
                "language": "en-US", "page": page},
        headers={"User-Agent": UA},
        timeout=25,
    )
    r.raise_for_status()
    data = r.json()
    results = data.get("results", []) if isinstance(data, dict) else []
    return [m for m in results if isinstance(m, dict)]


def _tgl_indo(iso: str) -> str:
    """'2026-10-02' -> '02 Okt 2026'. Gagal = apa adanya."""
    try:
        y, m, d = iso.split("-")
        return f"{int(d):02d} {_BULAN[int(m)]} {y}"
    except Exception:  # noqa: BLE001
        return iso or "-"


def format_upcoming_movies(region: str = "ID", limit: int = 20) -> str:
    """Teks film segera rilis (TMDB). Judul bisa diklik ke halaman TMDB."""
    try:
        movies = fetch_upcoming_movies(region)[:limit]
    except Exception as e:  # noqa: BLE001
        return f"Gagal ambil upcoming movies: {e}"
    blocks = [f"<b>Upcoming Movies ({(region or 'ID').upper()}, sumber: TMDB)</b>"]
    for n, m in enumerate(movies, 1):
        title = html.escape(str(m.get("title") or "?"))
        mid = m.get("id")
        if mid:
            title = f'<a href="https://www.themoviedb.org/movie/{mid}">{title}</a>'
        rel = _tgl_indo(str(m.get("release_date") or ""))
        rating = m.get("vote_average") or 0
        bit = f"rilis {rel}"
        if rating:
            bit += f" | Rating {rating:.1f}"
        blocks.append(f"{n}. {title} - {bit}")
    if not movies:
        blocks.append("(tidak ada)")
    return "\n".join(blocks)[:4000]


def _chunks(text: str, limit: int = 4000) -> list[str]:
    """Potong teks panjang per baris agar <= limit Telegram (4096)."""
    if len(text) <= limit:
        return [text]
    out, cur = [], ""
    for ln in text.split("\n"):
        if len(cur) + len(ln) + 1 > limit:
            out.append(cur)
            cur = ln
        else:
            cur = f"{cur}\n{ln}" if cur else ln
    if cur:
        out.append(cur)
    return out


def _get_updates(token: str, offset: int) -> list[dict]:
    r = requests.get(
        TG_API.format(token=token, method="getUpdates"),
        params={"offset": offset, "timeout": 0},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(str(data)[:200])
    return data.get("result", []) or []


def _route(cmd: str) -> str | None:
    """Return teks balasan, atau None kalau bukan command dikenal."""
    if cmd in ("/start", "/help"):
        return HELP_TEXT
    if cmd == "/upcoming_kdrama":
        return format_upcoming("KR", "Upcoming K-Drama & Acara Korea")
    if cmd == "/upcoming_series":
        return format_upcoming("US", "Upcoming Series US")
    if cmd == "/upcoming_movies":
        return format_upcoming_movies(Config.TMDB_REGION)
    if cmd.startswith("/"):
        return "Perintah tidak dikenal. Coba /help"
    return None


def handle_commands(send: bool = True) -> int:
    """Kuras antrian perintah Telegram dan balas. Return jumlah balasan."""
    if not Config.BOT_TOKEN or not Config.CHAT_IDS:
        return 0
    state = load(Config.STATE_FILE)
    offset = state.get("tg_offset", 0) or 0
    try:
        updates = _get_updates(Config.BOT_TOKEN, offset)
    except Exception as e:  # noqa: BLE001
        print(f"[commands] getUpdates gagal: {e}")
        return 0
    allowed = {str(c) for c in Config.CHAT_IDS}
    replied = 0
    for u in updates:
        offset = max(offset, int(u.get("update_id", 0)) + 1)
        msg = u.get("message") or {}
        chat_id = str((msg.get("chat") or {}).get("id", ""))
        text = str(msg.get("text") or "").strip()
        if not text.startswith("/"):
            continue
        cmd = text.split()[0].split("@")[0].lower()
        if chat_id not in allowed:
            print(f"[commands] abaikan {cmd} dari chat tak dikenal {chat_id}")
            continue
        reply = _route(cmd)
        if reply is None:
            continue
        if send:
            for chunk in _chunks(reply):
                if send_message(Config.BOT_TOKEN, chat_id, chunk):
                    replied += 1
                time.sleep(0.4)
            print(f"  [cmd] {cmd} -> {chat_id} ({replied} balas)")
        else:
            print(f"  [skip-kirim-cmd] {cmd} -> {chat_id}")
    state["tg_offset"] = offset
    if send:
        save(Config.STATE_FILE, state)
    return replied
