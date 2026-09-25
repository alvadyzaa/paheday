"""Kirim pesan ke Telegram via Bot HTTP API (tanpa lib tambahan)."""
from __future__ import annotations

import html
import re
import time

import requests

API = "https://api.telegram.org/bot{token}/{method}"

_HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
_BULAN = {
    "01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr", "05": "Mei",
    "06": "Jun", "07": "Jul", "08": "Agu", "09": "Sep", "10": "Okt",
    "11": "Nov", "12": "Des",
}


def format_waktu(s: str) -> str:
    """'2026-09-23 04:58:56' (WIB) -> 'Rabu, 23 Sep 2026 - 04:58 WIB'.
    Gagal parse = kembalikan apa adanya."""
    from datetime import datetime
    from email.utils import parsedate_to_datetime

    s = (s or "").strip()
    if not s:
        return "-"
    try:
        if "T" in s and ("+" in s or s.endswith("Z")):
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        elif re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}", s):
            dt = datetime.strptime(s[:16], "%Y-%m-%d %H:%M")
        else:  # format RSS: "Tue, 22 Sep 2026 13:25:00 +0000"
            dt = parsedate_to_datetime(s)
        day = _HARI[dt.weekday()]
        mon = _BULAN.get(f"{dt.month:02d}", f"{dt.month:02d}")
        return f"{day}, {dt.day:02d} {mon} {dt.year} - {dt.hour:02d}:{dt.minute:02d} WIB"
    except Exception:  # noqa: BLE001
        return s


def send_message(token: str, chat_id: str, text: str, retries: int = 3) -> bool:
    """Kirim pesan HTML. Return True jika ok."""
    url = API.format(token=token, method="sendMessage")
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    last_err = ""
    for attempt in range(retries):
        try:
            r = requests.post(url, json=payload, timeout=20)
            if r.status_code == 429:  # rate limited
                retry_after = 1
                try:
                    retry_after = r.json().get("parameters", {}).get("retry_after", 1)
                except Exception:
                    pass
                time.sleep(int(retry_after) + 1)
                continue
            r.raise_for_status()
            data = r.json()
            if data.get("ok"):
                return True
            last_err = str(data)
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            time.sleep(2 * (attempt + 1))
    print(f"[notify] gagal kirim ke {chat_id}: {last_err}")
    return False


def send_photo(token: str, chat_id: str, photo_url: str, caption: str, retries: int = 3) -> bool:
    """Kirim foto + caption HTML. Return True jika ok."""
    url = API.format(token=token, method="sendPhoto")
    payload = {
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": caption[:1000],
        "parse_mode": "HTML",
    }
    last_err = ""
    for attempt in range(retries):
        try:
            r = requests.post(url, json=payload, timeout=30)
            if r.status_code == 429:  # rate limited
                retry_after = 1
                try:
                    retry_after = r.json().get("parameters", {}).get("retry_after", 1)
                except Exception:
                    pass
                time.sleep(int(retry_after) + 1)
                continue
            data = r.json()
            if data.get("ok"):
                return True
            last_err = str(data)
            break  # error selain rate-limit (misal foto webp ditolak) -> fallback text
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            time.sleep(2 * (attempt + 1))
    print(f"[notify] sendPhoto gagal ke {chat_id}: {last_err}")
    return False


def broadcast(token: str, chat_ids: list[str], text: str) -> int:
    ok = 0
    for cid in chat_ids:
        if send_message(token, cid, text):
            ok += 1
        time.sleep(0.4)  # hindari rate-limit antar chat
    return ok


def broadcast_media(
    token: str, chat_ids: list[str], photo_url: str, caption: str, fallback_text: str
) -> int:
    """Kirim foto ke semua chat; kalau foto gagal, fallback ke pesan teks."""
    ok = 0
    for cid in chat_ids:
        if photo_url and send_photo(token, cid, photo_url, caption):
            ok += 1
        elif send_message(token, cid, fallback_text):
            ok += 1
        time.sleep(0.4)
    return ok


def format_post(source_label: str, post: dict, is_update: bool = False) -> str:
    title = html.escape(post.get("title", "(tanpa judul)"))
    link = html.escape(post.get("link", ""), quote=True)
    pub = html.escape(post.get("date", ""))
    mod = html.escape(post.get("modified", ""))
    cats = ", ".join(html.escape(c) for c in post.get("categories", [])[:6])
    desc = (post.get("description", "") or "").strip()
    if len(desc) > 220:
        desc = desc[:220].rstrip() + "…"
    desc = html.escape(desc)

    tag = "🔄 <b>UPDATE EPISODE</b>" if is_update else "🆕 <b>POST BARU</b>"
    lines = [f"{tag} — {source_label}", f'🎬 <a href="{link}"><b>{title}</b></a>']
    if cats:
        lines.append(f"🏷 {cats}")
    if desc:
        lines.append(f"\n{desc}")
    lines.append(f"\n🔗 {link}")
    lines.append(f"Diposting: {html.escape(format_waktu(pub))}")
    if mod and mod != pub:
        lines.append(f"Diupdate: {html.escape(format_waktu(mod))}")
    return "\n".join(lines)


def format_dramaday(post: dict, info: dict, is_update: bool = False) -> tuple[str, str]:
    """Return (caption, fallback_text) untuk dramaday.

    Caption ringkas (<=1000 char) untuk sendPhoto, fallback_text versi
    lengkap untuk sendMessage bila foto gagal terkirim.
    """
    title = html.escape(post.get("title", "(tanpa judul)"))
    link = html.escape(post.get("link", ""), quote=True)
    pub = post.get("date", "") or ""
    mod = post.get("modified", "") or ""

    tag = "UPDATE EPISODE" if is_update else "POST BARU"
    season = f" (S{info['season']})" if info.get("season") else ""
    if info.get("ep_end") and info.get("ep_end") != "0":
        if info.get("ep_total"):
            ep_line = f"Episode <b>{info['ep_end']}</b> dari {info['ep_total']} total"
        else:
            ep_line = f"Episode <b>{info['ep_start']}-{info['ep_end']}</b> tersedia"
    else:
        ep_line = ""

    meta_bits = []
    if info.get("status"):
        meta_bits.append(html.escape(info["status"]))
    if info.get("year"):
        meta_bits.append(html.escape(info["year"]))
    if info.get("network"):
        meta_bits.append(html.escape(info["network"]))

    lines = [f"<b>{tag}</b> - Dramaday.me", f"<b>{title}{season}</b>"]
    if ep_line:
        lines.append(ep_line)
    if meta_bits:
        lines.append(" | ".join(meta_bits))
    if info.get("airtime"):
        lines.append(html.escape(info["airtime"]))
    if info.get("genre"):
        lines.append(f"Genre: {html.escape(info['genre'])}")
    lines.append(f'<a href="{link}">Link download</a>')
    lines.append(f"Diposting: {html.escape(format_waktu(pub))}")
    if mod and mod != pub:
        lines.append(f"Diupdate: {html.escape(format_waktu(mod))}")
    caption = "\n".join(lines)

    # fallback teks: caption + sinopsis
    desc = (post.get("description", "") or "").strip()
    if len(desc) > 200:
        desc = desc[:200].rstrip() + "..."
    fallback = caption
    if desc and not info.get("is_ost"):
        fallback += f"\n\n{html.escape(desc)}"
    fallback += f"\n{link}"
    return caption, fallback


def format_pahe(post: dict, is_update: bool = False) -> tuple[str, str]:
    """Return (caption, fallback_text) untuk pahe.ink.

    post['categories'] = genre, post['tag_names'] = tag (tahun, kualitas, codec).
    """
    title = html.escape(post.get("title", "(tanpa judul)"))
    link = html.escape(post.get("link", ""), quote=True)
    pub = post.get("date", "") or ""
    mod = post.get("modified", "") or ""

    tag = "UPDATE" if is_update else "POST BARU"
    cats = [c for c in post.get("categories", []) if c][:5]
    tags = post.get("tag_names", []) or []

    year = next((t for t in tags if len(t) == 4 and t.isdigit()), "")
    quals = [t for t in tags if t.lower() in ("480p", "720p", "1080p", "2160p", "4k")]
    codecs = [t for t in tags if t.lower() in ("x264", "x265", "x266", "hevc")]

    bits = [b for b in [year, " | ".join(quals), " | ".join(codecs)] if b]

    lines = [f"<b>{tag}</b> - Pahe.ink", f"<b>{title}</b>"]
    if cats:
        lines.append("Genre: " + html.escape(", ".join(cats)))
    if bits:
        lines.append(html.escape(" | ".join(bits)))
    lines.append(f'<a href="{link}">Link download</a>')
    lines.append(f"Diposting: {html.escape(format_waktu(pub))}")
    if mod and mod != pub:
        lines.append(f"Diupdate: {html.escape(format_waktu(mod))}")
    caption = "\n".join(lines)

    desc = (post.get("description", "") or "").strip()
    if len(desc) > 200:
        desc = desc[:200].rstrip() + "..."
    fallback = caption
    if desc:
        fallback += f"\n\n{html.escape(desc)}"
    fallback += f"\n{link}"
    return caption, fallback


def format_n3x(post: dict, is_update: bool = False) -> tuple[str, str]:
    """Return (caption, fallback_text) untuk n3x.me.

    post['categories'] = genres, post['year']/['rating']/['duration']
    diisi fetcher. is_update praktis tidak pernah True (API tak punya
    modified), tapi tetap didukung demi konsistensi.
    """
    title = html.escape(post.get("title", "(tanpa judul)"))
    link = html.escape(post.get("link", ""), quote=True)
    pub = post.get("date", "") or ""
    kind = (post.get("kind") or "movie").lower()
    kind_label = "Series" if kind in ("series", "tv", "show") else "Movie"

    tag = "UPDATE" if is_update else "POST BARU"
    cats = [c for c in post.get("categories", []) if c][:5]

    bits = []
    if post.get("year"):
        bits.append(str(post["year"]))
    if post.get("rating"):
        bits.append(f"Rating {post['rating']}")
    if post.get("duration"):
        bits.append(str(post["duration"]))

    lines = [f"<b>{tag}</b> - N3x.me ({kind_label})", f"<b>{title}</b>"]
    if cats:
        lines.append("Genre: " + html.escape(", ".join(cats)))
    if bits:
        lines.append(html.escape(" | ".join(bits)))
    lines.append(f'<a href="{link}">Link streaming/download</a>')
    lines.append(f"Diposting: {html.escape(format_waktu(pub))}")
    caption = "\n".join(lines)

    desc = (post.get("description", "") or "").strip()
    if len(desc) > 200:
        desc = desc[:200].rstrip() + "..."
    fallback = caption
    if desc:
        fallback += f"\n\n{html.escape(desc)}"
    fallback += f"\n{link}"
    return caption, fallback


def test_connection(token: str) -> tuple[bool, str]:
    try:
        r = requests.get(API.format(token=token, method="getMe"), timeout=15)
        data = r.json()
        if data.get("ok"):
            u = data["result"]
            return True, f"@{u.get('username')} (id={u.get('id')})"
        return False, str(data)
    except Exception as e:  # noqa: BLE001
        return False, str(e)
