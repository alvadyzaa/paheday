"""Slash-command handler Telegram (getUpdates drain, tanpa server/webhook).

Perintah yang didukung:
    /start, /help            -> bantuan
    /searchpahe <judul>      -> cari film/series di pahe.ink
    /searchdrama <judul>     -> cari drama/OST di dramaday.me
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
from notify import answer_callback, send_message
from sources import (
    extract_dramaday_downloads,
    extract_pahe_downloads,
    fetch_post_detail,
    search_wp,
)
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
    "/searchpahe &lt;judul&gt; - cari film/series di pahe.ink\n"
    "/searchdrama &lt;judul&gt; - cari drama/OST di dramaday.me\n"
    "(ketuk nomor hasil untuk link download langsung)\n"
    "/upcoming_kdrama - jadwal tayang Korea (hari ini + besok)\n"
    "/upcoming_series - jadwal tayang US (hari ini + besok)\n"
    "/upcoming_movies - film segera rilis (TMDB)\n"
    "/releases_today - rilis hari ini (digest manual)\n"
    "/help - pesan ini"
)


def format_search(base_url: str, query: str, label: str, limit: int = 8) -> tuple[str, dict | None]:
    """Hasil pencarian WP sebagai daftar judul yang bisa diklik.

    Return (teks, reply_markup). Markup berisi tombol nomor 1..N; ketuk
    nomor untuk menerima link download langsung post tersebut.
    """
    src = "pahe" if "pahe" in base_url else "dd"
    if not query.strip():
        return (f"Format: /{'searchpahe' if src == 'pahe' else 'searchdrama'} &lt;judul&gt;", None)
    try:
        results = search_wp(base_url, query, limit)
    except Exception as e:  # noqa: BLE001
        return (f"Gagal mencari di {label}: {e}", None)
    if not results:
        return (f'Tidak ada hasil untuk "<b>{html.escape(query)}</b>" di {label}.', None)
    blocks = [f'Hasil pencarian "{html.escape(query)}" di {label} ({len(results)}):']
    buttons = []
    for n, r in enumerate(results, 1):
        title = html.escape(r["title"] or "(tanpa judul)")
        link = html.escape(r["link"], quote=True)
        blocks.append(f'{n}. <a href="{link}">{title}</a>')
        if r.get("id"):
            buttons.append({"text": str(n), "callback_data": f"{src}:{r['id']}"})
    blocks.append("\nKetuk nomor untuk link download langsung.")
    markup = {"inline_keyboard": [buttons]} if buttons else None
    return ("\n".join(blocks)[:4000], markup)


def format_pahe_links(title: str, groups: list[dict]) -> str:
    """Link download langsung pahe, dikelompokkan per kualitas."""
    if not groups:
        return f"<b>{html.escape(title)}</b>\nLink download tidak ketemu di post. Buka halaman post-nya."
    lines = [f"<b>{html.escape(title)}</b>", "Link download langsung:"]
    for g in groups:
        hosts = " | ".join(
            f'<a href="{html.escape(u, quote=True)}">{html.escape(h)}</a>'
            for h, u in g["links"]
        )
        block = f"\n<b>{html.escape(g['quality'])} | {html.escape(g['size'])}</b>\n{hosts}"
        if len("\n".join(lines)) + len(block) > 3800:
            lines.append("(dipotong — buka post untuk sisanya)")
            break
        lines.append(block)
    return "\n".join(lines)


def format_dramaday_links(title: str, dl: dict) -> str:
    """Link download langsung dramaday per baris episode."""
    rows = dl.get("rows", [])
    if not rows:
        return f"<b>{html.escape(title)}</b>\nLink download tidak ketemu di post. Buka halaman post-nya."
    lines = [f"<b>{html.escape(title)}</b>", "Link download langsung:"]
    skipped = 0
    show = rows if len(rows) <= 4 else rows[-3:]
    skipped = len(rows) - len(show)
    if skipped:
        lines.append(f"({skipped} episode sebelumnya — buka post untuk lengkap)")
    for r in show:
        hosts = " | ".join(
            f'<a href="{html.escape(u, quote=True)}">{html.escape(h)}</a>'
            for h, u in r["links"][:8]
        )
        block = f"\nEp {html.escape(r['eps'])}: {hosts}"
        if len("\n".join(lines)) + len(block) > 3800:
            lines.append("(dipotong — buka post untuk sisanya)")
            break
        lines.append(block)
    return "\n".join(lines)


def handle_download_callback(src: str, pid: str) -> str:
    """Ambil link download langsung satu post. src: 'pahe' | 'dd'."""
    base = Config.PAHE_URL if src == "pahe" else Config.DRAMADAY_URL
    try:
        detail = fetch_post_detail(base, pid)
    except Exception as e:  # noqa: BLE001
        return f"Gagal ambil post: {e}"
    title = detail.get("title", "") or "Post"
    if src == "pahe":
        return format_pahe_links(title, extract_pahe_downloads(detail.get("content", "")))
    info_rows = extract_dramaday_downloads(detail.get("content", ""))
    return format_dramaday_links(title, info_rows)


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
        d = _today_wib() + timedelta(days=i)
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


def _today_wib() -> date:
    """Tanggal hari ini dalam WIB (runner Actions = UTC, jadi +7 jam manual)."""
    from datetime import datetime, timedelta as _td, timezone

    return (datetime.now(timezone.utc) + _td(hours=7)).date()


def fetch_movies_released_on(day: date, region: str = "ID") -> list[dict]:
    """Film yang rilis TEPAT pada tanggal tsb via TMDB discover.
    Tanpa key -> list kosong (section movies dilewat, bukan error)."""
    if not Config.TMDB_API_KEY:
        return []
    r = requests.get(
        f"{TMDB_API}/discover/movie",
        params={"api_key": Config.TMDB_API_KEY, "region": (region or "ID").upper(),
                "language": "en-US", "primary_release_date.gte": day.isoformat(),
                "primary_release_date.lte": day.isoformat(),
                "sort_by": "popularity.desc", "include_adult": "false", "page": 1},
        headers={"User-Agent": UA},
        timeout=25,
    )
    r.raise_for_status()
    data = r.json()
    results = data.get("results", []) if isinstance(data, dict) else []
    return [m for m in results if isinstance(m, dict)]


def _movie_digest_line(n: int, m: dict) -> str:
    title = html.escape(str(m.get("title") or "?"))
    mid = m.get("id")
    if mid:
        title = f'<a href="https://www.themoviedb.org/movie/{mid}">{title}</a>'
    rating = m.get("vote_average") or 0
    bit = f"Rating {rating:.1f}" if rating else ""
    return f"{n}. {title}" + (f" - {bit}" if bit else "")


def build_release_digest(day: date | None = None, tv_limit: int = 15, movie_limit: int = 15) -> str:
    """Notif NEW RELEASE: tayang/rilis hari ini (WIB). '' kalau kosong."""
    d = day or _today_wib()
    hari = f"{_HARI[d.weekday()]}, {d.day} {_BULAN[d.month]} {d.year}"
    sections: list[str] = []
    # US khusus Scripted/Animation (buang reality/daytime); KR tetap longgar
    # karena variety Korea termasuk konten yang dicari.
    allow = {"KR": None, "US": {"scripted", "animation"}}
    for country, judul in (("KR", "K-Drama & Acara Korea"), ("US", "Series US")):
        try:
            items = fetch_schedule(country, d)
        except Exception:  # noqa: BLE001
            items = []
        lines: list[str] = []
        for it in items:
            show = it.get("show", {}) or {}
            only = allow.get(country)
            if only is not None and str(show.get("type") or "").lower() not in only:
                continue
            line = _show_line(it)
            if line:
                lines.append(line)
            if len(lines) >= tv_limit:
                break
        if lines:
            sections.append(f"\n<b>{judul}</b>")
            sections.extend(f"{n + 1}. {ln}" for n, ln in enumerate(lines))
    try:
        movies = fetch_movies_released_on(d, Config.TMDB_REGION)[:movie_limit]
    except Exception:  # noqa: BLE001
        movies = []
    if movies:
        sections.append("\n<b>Movies</b>")
        sections.extend(_movie_digest_line(n + 1, m) for n, m in enumerate(movies))
    if not sections:
        return ""
    return (f"<b>NEW RELEASE - {hari}</b>\n" + "\n".join(sections))[:4000]


def maybe_send_release_digest(send: bool = True) -> int:
    """Kirim digest NEW RELEASE sekali per hari (WIB). Return jml chat."""
    if not Config.BOT_TOKEN or not Config.CHAT_IDS:
        return 0
    state = load(Config.STATE_FILE)
    today = _today_wib().isoformat()
    if state.get("release_digest") == today:
        return 0
    text = build_release_digest()
    if not text:
        print("[digest] tidak ada rilis hari ini, skip")
        return 0
    n = 0
    if send:
        for cid in Config.CHAT_IDS:
            ok = False
            for chunk in _chunks(text):
                if send_message(Config.BOT_TOKEN, cid, chunk):
                    ok = True
                time.sleep(0.4)
            if ok:
                n += 1
        print(f"  [digest] NEW RELEASE -> {n} chat")
    else:
        print("  [skip-kirim-digest]")
    state["release_digest"] = today
    if send:
        save(Config.STATE_FILE, state)
    return n


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


def _route(cmd: str, args: str = "") -> tuple[str, dict | None]:
    """Return (teks balasan, reply_markup), teks None kalau bukan command."""
    if cmd in ("/start", "/help"):
        return (HELP_TEXT, None)
    if cmd == "/searchpahe":
        return format_search(Config.PAHE_URL, args, "Pahe.ink")
    if cmd == "/searchdrama":
        return format_search(Config.DRAMADAY_URL, args, "Dramaday.me")
    if cmd == "/upcoming_kdrama":
        return (format_upcoming("KR", "Upcoming K-Drama & Acara Korea"), None)
    if cmd == "/upcoming_series":
        return (format_upcoming("US", "Upcoming Series US"), None)
    if cmd == "/upcoming_movies":
        return (format_upcoming_movies(Config.TMDB_REGION), None)
    if cmd == "/releases_today":
        return (build_release_digest() or "(belum ada rilis hari ini)", None)
    if cmd.startswith("/"):
        return ("Perintah tidak dikenal. Coba /help", None)
    return (None, None)


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
        # --- tombol inline (ketuk nomor hasil search) ---
        cb = u.get("callback_query") or {}
        if cb:
            chat_id = str(((cb.get("message") or {}).get("chat") or {}).get("id", ""))
            data = str(cb.get("data") or "")
            if chat_id in allowed and ":" in data:
                src, pid = data.split(":", 1)
                if src in ("pahe", "dd") and pid.strip().isdigit():
                    answer_callback(Config.BOT_TOKEN, str(cb.get("id", "")), "Mengambil link...")
                    reply = handle_download_callback(src, pid.strip())
                    if send:
                        for chunk in _chunks(reply):
                            if send_message(Config.BOT_TOKEN, chat_id, chunk):
                                replied += 1
                            time.sleep(0.4)
                        print(f"  [cmd] tombol {data} -> {chat_id}")
                    else:
                        print(f"  [skip-kirim-cb] {data} -> {chat_id}")
            continue
        msg = u.get("message") or {}
        chat_id = str((msg.get("chat") or {}).get("id", ""))
        text = str(msg.get("text") or "").strip()
        if not text.startswith("/"):
            continue
        cmd = text.split()[0].split("@")[0].lower()
        args = text.split(" ", 1)[1].strip() if " " in text else ""
        if chat_id not in allowed:
            print(f"[commands] abaikan {cmd} dari chat tak dikenal {chat_id}")
            continue
        reply, markup = _route(cmd, args)
        if reply is None:
            continue
        if send:
            first = True
            for chunk in _chunks(reply):
                if send_message(Config.BOT_TOKEN, chat_id, chunk,
                                reply_markup=markup if first else None):
                    replied += 1
                first = False
                time.sleep(0.4)
            print(f"  [cmd] {cmd} -> {chat_id} ({replied} balas)")
        else:
            print(f"  [skip-kirim-cmd] {cmd} -> {chat_id}")
    state["tg_offset"] = offset
    if send:
        save(Config.STATE_FILE, state)
    return replied
