"""Fetcher pahe.ink & dramaday.me.

Strategi utama: WP REST API orderby=modified (menangkap post baru
MAUPUN update episode pada post lama — kasus umum dramaday.me).
Fallback: RSS /feed/ kalau wp-json diblokir Cloudflare (403/challenge).

Mode detail (dramaday): ikut ambil content + poster (featuredmedia)
lalu parse info drama (episode, status, network, tahun, dst).
"""
from __future__ import annotations

import re
import time

import feedparser
import requests
from html import unescape as _unescape

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

CF_HINTS = ("just a moment", "attention required", "cf-chl", "cloudflare")

# fallback kalau endpoint taxonomy tidak bisa diakses (jarang berubah)
_DRAMADAY_CATS_FALLBACK = {
    3: "Drama", 4: "Completed", 5: "Ongoing",
    273: "OST", 434: "Variety Show", 692: "Movie", 565: "Guide",
}

_taxonomy_cache: dict = {}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": BROWSER_UA,
            "Accept": "application/json, application/xml, text/xml, */*",
            "Accept-Language": "en-US,en;q=0.9,id;q=0.7",
            "Referer": "https://www.google.com/",
        }
    )
    return s


def _is_cf_block(text: str) -> bool:
    low = text.lower()
    return any(h in low for h in CF_HINTS)


def _strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _to_wib(gmt_str: str) -> str:
    """'2026-09-23T00:53:06' (GMT) -> '2026-09-23 07:53:06' (WIB).
    Gagal parse = kembalikan apa adanya."""
    from datetime import datetime, timedelta

    try:
        dt = datetime.strptime((gmt_str or "")[:19], "%Y-%m-%dT%H:%M:%S")
        return (dt + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001
        return (gmt_str or "").replace("T", " ")


def _rss_to_wib(pub: str) -> str:
    """'Tue, 22 Sep 2026 13:25:00 +0000' -> '2026-09-22 20:25:00' (WIB)."""
    from datetime import timedelta, timezone
    from email.utils import parsedate_to_datetime

    try:
        dt = parsedate_to_datetime(pub).astimezone(timezone.utc)
        return (dt + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001
        return pub


def _get(url: str, timeout: int = 25) -> requests.Response:
    """GET dengan retry + deteksi challenge Cloudflare."""
    s = _session()
    last_err = ""
    for attempt in range(3):
        try:
            r = s.get(url, timeout=timeout)
            if r.status_code in (403, 503) or _is_cf_block(r.text[:2000]):
                last_err = f"HTTP {r.status_code} (kemungkinan Cloudflare challenge)"
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"GET gagal [{url}]: {last_err}")


# ---------------- WP JSON ----------------

def fetch_wp_json(
    base_url: str, per_page: int = 15, detail: bool = False
) -> tuple[list[dict], str]:
    """Return (posts, method). Raises RuntimeError kalau gagal total.

    detail=True ikut ambil content (untuk parsing info dramaday).
    Poster diambil terpisah via fetch_post_detail karena _embed tidak
    stabil di endpoint list ketika dikombinasi _fields.
    """
    fields = "id,date_gmt,modified_gmt,link,title,excerpt,categories,tags"
    if detail:
        fields += ",content"
    url = (
        f"{base_url}/wp-json/wp/v2/posts"
        f"?per_page={per_page}&orderby=modified&order=desc"
        f"&_fields={fields}"
    )
    items = _get(url).json()
    posts = []
    for it in items:
        title = _unescape(_strip_html(it.get("title", {}).get("rendered", "")))
        post = {
            "id": str(it.get("id")),
            "title": title,
            "link": it.get("link", ""),
            "date": _to_wib(it.get("date_gmt", "")),
            "modified": _to_wib(it.get("modified_gmt", "")),
            "description": _unescape(_strip_html(
                it.get("excerpt", {}).get("rendered", "")
            ))[:300],
            "categories": [],       # diisi nama saat mode detail
            "cat_ids": it.get("categories", []) or [],
            "tag_ids": it.get("tags", []) or [],
            "poster": "",
        }
        if detail:
            post["content"] = it.get("content", {}).get("rendered", "")
        posts.append(post)
    return posts, "wp-json"


def fetch_post_detail(base_url: str, post_id: str) -> dict:
    """Ambil content + poster satu post (endpoint single, tanpa _fields
    agar _embed featuredmedia selalu ikut)."""
    try:
        it = _get(f"{base_url}/wp-json/wp/v2/posts/{post_id}?_embed").json()
    except Exception:  # noqa: BLE001
        return {"content": "", "poster": ""}
    poster = ""
    try:
        media = it.get("_embedded", {}).get("wp:featuredmedia", [])
        if media:
            poster = media[0].get("source_url", "") or ""
    except Exception:  # noqa: BLE001
        poster = ""
    content = it.get("content", {}).get("rendered", "")
    return {"content": content, "poster": poster}


# ---------------- RSS fallback ----------------

def fetch_rss(base_url: str) -> tuple[list[dict], str]:
    parsed = feedparser.parse(
        f"{base_url}/feed/",
        agent=BROWSER_UA,
        request_headers={"Referer": "https://www.google.com/"},
    )
    if parsed.bozo and not parsed.entries:
        raise RuntimeError(f"RSS gagal [{base_url}]: {parsed.bozo_exception}")
    posts = []
    for e in parsed.entries[:30]:
        pid = e.get("id") or e.get("guid") or e.get("link", "")
        pub = e.get("published") or e.get("updated") or ""
        wib = _rss_to_wib(pub)
        cats = [t.get("term", "") for t in e.get("tags", [])] if e.get("tags") else []
        posts.append(
            {
                "id": pid,  # RSS tidak punya numeric id stabil → pakai guid/link
                "title": _unescape((e.get("title") or "").strip()),
                "link": e.get("link", ""),
                "date": wib,
                "modified": wib,  # RSS tidak tahu modified → samakan
                "description": _strip_html(e.get("summary", ""))[:300],
                "categories": cats,
                "cat_ids": [],
                "tag_ids": [],
                "poster": "",
                "content": "",
            }
        )
    return posts, "rss"


def fetch_source(
    name: str, base_url: str, per_page: int = 15, detail: bool = False
) -> tuple[list[dict], str]:
    """Coba wp-json dulu, fallback ke RSS. Return (posts, method)."""
    try:
        return fetch_wp_json(base_url, per_page, detail=detail)
    except Exception as e:  # noqa: BLE001
        print(f"[{name}] wp-json gagal ({e}), fallback ke RSS...")
        return fetch_rss(base_url)


# ---------------- n3x.me (Eyrda, JSON API, bukan WordPress) ----------------

def _iso_to_wib(iso: str) -> str:
    """'2026-09-25T11:29:43.000Z' (UTC) -> '2026-09-25 18:29:43' (WIB).
    Gagal parse = kembalikan apa adanya."""
    from datetime import timedelta, timezone
    from datetime import datetime as _dt

    try:
        dt = _dt.fromisoformat((iso or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc) + timedelta(hours=7)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001
        return (iso or "").replace("T", " ")


def fetch_n3x(base_url: str, per_page: int = 15) -> tuple[list[dict], str]:
    """Ambil rilisan terbaru n3x.me via /api/posts (sudah urut terbaru).
    Return (posts, method). Raises RuntimeError kalau gagal total.

    API tidak punya konsep modified -> date == modified, sehingga hanya
    postingan BARU yang memicu notif (update tidak pernah fire). Ini juga
    otomatis kebal spam postingan lama seperti kasus pahe 2018.
    """
    data = _get(f"{base_url}/api/posts?page=1&limit={per_page}").json()
    items = data.get("posts", []) if isinstance(data, dict) else data
    posts = []
    for it in (items or [])[:per_page]:
        pid = str(it.get("id", ""))
        if not pid:
            continue
        slug = (it.get("slug") or "").strip()
        typ = str(it.get("type") or "movie").lower()
        path = "series" if typ in ("series", "tv", "show") else "movie"
        link = f"{base_url}/{path}/{pid}-{slug}" if slug else base_url
        cover = it.get("cover_image_url") or ""
        if cover.startswith("/"):
            cover = base_url + cover
        wib = _iso_to_wib(it.get("created_at", ""))
        genres = [g.strip() for g in str(it.get("genres") or "").split(",") if g.strip()]
        year = str(it.get("release_year") or "").strip()
        title = _unescape((it.get("title") or "").strip())
        if year:
            title = f"{title} ({year})"
        rating = it.get("tmdb_rating") or it.get("rating") or ""
        posts.append(
            {
                "id": pid,
                "title": title,
                "link": link,
                "date": wib,
                "modified": wib,  # API tak mengenal modified -> samakan
                "description": _unescape(_strip_html(it.get("description") or ""))[:300],
                "categories": genres,
                "cat_ids": [],
                "tag_ids": [],
                "poster": cover,
                "year": year,
                "rating": str(rating),
                "duration": (it.get("duration") or "").strip(),
                "kind": typ,
            }
        )
    return posts, "n3x-api"


# ---------------- taxonomy (nama kategori / tag) ----------------

def get_taxonomy_map(base_url: str) -> dict:
    """Return {'categories': {id: name}, 'tags': {id: name}}. Cache per proses."""
    if base_url in _taxonomy_cache:
        return _taxonomy_cache[base_url]
    result: dict = {"categories": {}, "tags": {}}
    try:
        cats = _get(
            f"{base_url}/wp-json/wp/v2/categories?per_page=100&_fields=id,name"
        ).json()
        result["categories"] = {int(c["id"]): c["name"] for c in cats}
    except Exception:  # noqa: BLE001
        if "dramaday" in base_url:
            result["categories"] = dict(_DRAMADAY_CATS_FALLBACK)
    # tags jumlahnya ratusan: resolve on-demand via ?include= (lihat resolve_year)
    _taxonomy_cache[base_url] = result
    return result


def resolve_tag_names(base_url: str, tag_ids: list[int]) -> list[str]:
    if not tag_ids:
        return []
    try:
        ids = ",".join(str(int(i)) for i in tag_ids[:20])
        tags = _get(
            f"{base_url}/wp-json/wp/v2/tags?include={ids}&per_page=100&_fields=id,name"
        ).json()
        return [t["name"] for t in tags]
    except Exception:  # noqa: BLE001
        return []


# ---------------- parser detail dramaday ----------------

def _field(text: str, name: str, stop: str) -> str:
    m = re.search(rf"{name}:\s*(.+?)\s*{stop}", text)
    return m.group(1).strip() if m else ""


def parse_dramaday_detail(post: dict, cat_names: list[str]) -> dict:
    """Parse content dramaday jadi info terstruktur. Aman untuk OST/variety
    (field yang tidak ada = string kosong)."""
    raw = post.get("content", "") or ""
    text = _unescape(re.sub(r"<[^>]+>", " ", raw))
    text = re.sub(r"\s+", " ", text)

    genre = _field(text, "Genre", "Episodes:")
    episodes_raw = _field(text, "Episodes", "Broadcast")
    network = _field(text, "Broadcast network", "Broadcast period:")
    period = _field(text, "Broadcast period", "Air time:")
    m_air = re.search(r"Air time:\s*(.+?)\s*(?:\[|Download Cast|Download$)", text)
    airtime = m_air.group(1).strip() if m_air else ""

    # --- episode terbaru yang sudah diupload ---
    # Tabel download punya 3 variasi baris:
    #   "Season 4 01-20 1080p ..."  (multi-season, range)
    #   "Episode Quality Download 01-08 1080p ..."  (single, range)
    #   "Episode Quality Download 01 1080p ... 02 1080p ... 14 1080p ..." (per-episode)
    # Prinsip: baris episode selalu diawali angka + kualitas, ambil baris TERAKHIR.
    season = ep_start = ep_end = ""
    seg_start = text.find("Episode Quality Download")
    if seg_start >= 0:
        seg_end = text.find("Learn How to download", seg_start)
        seg = text[seg_start:seg_end if seg_end > 0 else seg_start + 8000]
        rows = re.findall(
            r"(?<!\w)(\d{1,3})(?:\s*[-–]\s*(\d{1,3}))?\s+(?:1080p|720p|540p|480p)",
            seg,
        )
        if rows:
            ep_start, ep_end = rows[-1][0], rows[-1][1] or rows[-1][0]
        seasons = re.findall(r"Season\s+(\d+)", seg)
        if seasons:
            season = seasons[-1]

    # --- total episode ---
    ep_total = ""
    if season and episodes_raw:
        m_se = re.search(rf"S0*{int(season)}\s*:\s*(\d+)", episodes_raw)
        if m_se:
            ep_total = m_se.group(1)
    if not ep_total and episodes_raw and re.fullmatch(r"\d+", episodes_raw.strip()):
        ep_total = episodes_raw.strip()

    # --- tahun: dari broadcast period, fallback tag tahun ----
    year = ""
    m_y = re.search(r"(19|20)\d{2}", period)
    if m_y:
        year = m_y.group(0)

    # --- status ---
    status = ""
    for c in cat_names:
        if c.lower() in ("ongoing", "completed"):
            status = c
            break

    return {
        "genre": genre,
        "episodes_raw": episodes_raw,
        "network": network,
        "period": period,
        "airtime": airtime,
        "season": season,
        "ep_start": ep_start.lstrip("0") or "0",
        "ep_end": ep_end.lstrip("0") or "0",
        "ep_total": ep_total,
        "year": year,  # bisa dilengkapi via tags di main.py
        "status": status,
        "is_ost": "OST" in cat_names,
    }


# ---------------- filter ----------------

def match_filter(title: str, cats: list[str], include: list[str], exclude: list[str]) -> bool:
    hay = f"{title} {' '.join(cats)}".lower()
    if exclude and any(k in hay for k in exclude):
        return False
    if include and not any(k in hay for k in include):
        return False
    return True


def post_age_days(date_wib: str) -> float | None:
    """Umur postingan dalam hari berdasar tanggal publish (WIB).

    Input format WIB dari fetcher: 'YYYY-MM-DD HH:MM:SS'.
    Return None kalau format tidak dikenali (anggap recent agar tidak ke-skip).
    Dipakai untuk anti-spam: postingan lama (mis. 2018) yang ke-touch
    `modified`-nya tidak boleh memicu notif lagi.
    """
    from datetime import datetime

    s = (date_wib or "").strip()
    if not s:
        return None
    try:
        # format utama WIB: '2026-09-23 07:53:06' (boleh ada detik hilang)
        dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001
        try:
            dt = datetime.strptime(s[:16], "%Y-%m-%d %H:%M")
        except Exception:  # noqa: BLE001
            return None
    return (datetime.now() - dt).total_seconds() / 86400
