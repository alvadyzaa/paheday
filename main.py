"""PaheDay — Telegram notifier untuk pahe.ink, dramaday.me & n3x.me.

Usage:
    python main.py            # loop polling
    python main.py --once     # cek sekali lalu keluar
    python main.py --init     # isi state tanpa kirim (baseline pertama)
    python main.py --test     # tes koneksi telegram + fetch, tanpa kirim/state
"""
from __future__ import annotations

import argparse
import time
import traceback

from config import Config
from notify import (
    broadcast,
    broadcast_media,
    format_dramaday,
    format_n3x,
    format_pahe,
    format_post,
    send_message,
    test_connection,
)
from sources import (
    fetch_post_detail,
    fetch_n3x,
    fetch_source,
    get_taxonomy_map,
    match_filter,
    parse_dramaday_detail,
    post_age_days,
    resolve_tag_names,
)
from storage import load, save

SOURCES = [
    {
        "key": "pahe",
        "label": "Pahe.ink",
        "base": Config.PAHE_URL,
        "include": Config.PAHE_INCLUDE,
        "exclude": Config.PAHE_EXCLUDE,
        "detail": True,  # kirim format kaya + foto poster
        "info": False,   # tidak perlu parse content (judul pahe sudah lengkap)
        "enrich": True,   # WP: resolve kategori/tag + poster
        # Pahe = film sekali-post: update lama hampir selalu noise -> OFF,
        # dan hanya postingan baru (<= PAHE_MAX_AGE_DAYS) yang boleh notif.
        "notify_updates": Config.PAHE_NOTIFY_UPDATES,
        "max_age_days": Config.PAHE_MAX_AGE_DAYS,
    },
    {
        "key": "dramaday",
        "label": "Dramaday.me",
        "base": Config.DRAMADAY_URL,
        "include": Config.DRAMADAY_INCLUDE,
        "exclude": Config.DRAMADAY_EXCLUDE,
        "detail": True,  # ambil content + poster, kirim format kaya + foto
        "info": True,    # parse info drama (episode, network, dst)
        # Dramaday update = episode baru -> ikut DRAMADAY_NOTIFY_UPDATES,
        # tapi tetap hanya drama recent (<= DRAMADAY_MAX_AGE_DAYS).
        "notify_updates": Config.DRAMADAY_NOTIFY_UPDATES,
        "max_age_days": Config.DRAMADAY_MAX_AGE_DAYS,
        "enrich": True,    # WP: resolve kategori/tag + content/poster
    },
    {
        "key": "n3x",
        "label": "N3x.me",
        "base": Config.N3X_URL,
        "include": Config.N3X_INCLUDE,
        "exclude": Config.N3X_EXCLUDE,
        "detail": True,    # kirim format kaya + cover sebagai foto
        "info": False,     # tidak perlu parse content (API sudah terstruktur)
        "enrich": False,   # API JSON: genre/cover/tahun sudah ada di post
        # API tidak punya modified -> hanya BARU yang fire (kebal spam lama).
        "notify_updates": Config.N3X_NOTIFY_UPDATES,
        "max_age_days": Config.N3X_MAX_AGE_DAYS,
    },
]


def _enrich_dramaday(post: dict, tax: dict, base_url: str, with_info: bool = True) -> tuple[dict, list[str]]:
    """Lengkapi post: nama kategori/tag + poster (+ content & info terparse
    kalau with_info=True)."""
    cat_map = tax.get("categories", {})
    cat_names = [cat_map.get(int(i), "") for i in post.get("cat_ids", [])]
    cat_names = [c for c in cat_names if c]
    post["categories"] = cat_names
    post["tag_names"] = resolve_tag_names(
        base_url, [int(i) for i in post.get("tag_ids", [])]
    )
    # content + poster diambil on-demand (hanya untuk post baru/update)
    detail = fetch_post_detail(base_url, post["id"])
    if with_info:
        post["content"] = detail.get("content", "")
    post["poster"] = detail.get("poster", "")
    info: dict = parse_dramaday_detail(post, cat_names) if with_info else {}
    if with_info and not info["year"]:
        # fallback: cari tag berbentuk tahun (mis. "2026")
        for t in post["tag_names"]:
            if len(t) == 4 and t.isdigit():
                info["year"] = t
                break
    return info, cat_names


def check_once(send: bool = True) -> int:
    """Satu putaran cek. Return jumlah notif terkirim."""
    state = load(Config.STATE_FILE)
    total_sent = 0
    tax_cache: dict = {}

    for src in SOURCES:
        key, label = src["key"], src["label"]
        detail = src.get("detail", False)
        notify_updates = src.get("notify_updates", Config.NOTIFY_UPDATES)
        max_age = src.get("max_age_days", 0) or 0
        seen: dict = state.get(key, {})
        try:
            if key == "n3x":
                posts, method = fetch_n3x(src["base"], Config.PER_PAGE)
            else:
                posts, method = fetch_source(key, src["base"], Config.PER_PAGE)
            print(f"[{label}] {len(posts)} post via {method}")
        except Exception as e:  # noqa: BLE001
            print(f"[{label}] GAGAL total: {e}")
            continue

        if src.get("enrich", False) and key not in tax_cache:
            tax_cache[key] = get_taxonomy_map(src["base"])

        new_state = dict(seen)
        # kirim dari yang terlama agar pesan urut kronologis
        for post in reversed(posts):
            pid = post["id"]
            mod = post["modified"]
            old_mod = seen.get(pid)
            is_new = old_mod is None
            is_update = (not is_new) and (old_mod != mod)

            new_state[pid] = mod

            # --- Anti-spam postingan lama ---
            # orderby=modified membuat postingan 2018 yang ke-touch naik ke atas.
            # Patokan "recent" = tanggal publish (date), BUKAN modified.
            # State tetap di-update di atas agar tidak spam berulang.
            if max_age and max_age > 0:
                age = post_age_days(post.get("date", ""))
                if age is not None and age > max_age:
                    kind = "UPDATE" if is_update else "BARU"
                    print(
                        f"  [skip-tua] {kind} umur {age:.0f} hari"
                        f" > {max_age} hari: {post['title'][:70]}"
                    )
                    continue

            if is_new or (is_update and notify_updates):
                info, cat_names = ({}, [])
                if src.get("enrich", False):
                    info, cat_names = _enrich_dramaday(
                        post, tax_cache[key], src["base"],
                        with_info=src.get("info", False),
                    )
                if not match_filter(
                    post["title"], post.get("categories", []),
                    src["include"], src["exclude"],
                ):
                    continue
                if send:
                    if detail:
                        if key == "dramaday":
                            caption, fallback = format_dramaday(
                                post, info, is_update=is_update
                            )
                        elif key == "n3x":
                            caption, fallback = format_n3x(post, is_update=is_update)
                        else:
                            caption, fallback = format_pahe(post, is_update=is_update)
                        n = broadcast_media(
                            Config.BOT_TOKEN, Config.CHAT_IDS,
                            post.get("poster", ""), caption, fallback,
                        )
                    else:
                        text = format_post(label, post, is_update=is_update)
                        n = broadcast(Config.BOT_TOKEN, Config.CHAT_IDS, text)
                    total_sent += n
                    kind = "UPDATE" if is_update else "BARU"
                    print(f"  [{kind}] {post['title'][:70]} -> {n} chat")
                else:
                    print(f"  [skip-kirim] {post['title'][:70]}")

        state[key] = new_state

    if send:
        save(Config.STATE_FILE, state)
    return total_sent


def do_init() -> None:
    """Tandai semua post saat ini sebagai sudah-dilihat (tanpa kirim)."""
    state = load(Config.STATE_FILE)
    for src in SOURCES:
        try:
            if src["key"] == "n3x":
                posts, method = fetch_n3x(src["base"], Config.PER_PAGE)
            else:
                posts, method = fetch_source(src["key"], src["base"], Config.PER_PAGE)
            state[src["key"]] = {p["id"]: p["modified"] for p in posts}
            print(f"[{src['label']}] init {len(posts)} post via {method}")
        except Exception as e:  # noqa: BLE001
            print(f"[{src['label']}] init gagal: {e}")
    save(Config.STATE_FILE, state)
    print(f"State tersimpan -> {Config.STATE_FILE}. Mulai sekarang hanya yang baru dikirim.")


def do_test() -> None:
    ok, info = test_connection(Config.BOT_TOKEN)
    print(f"[telegram] getMe: {'OK ' + info if ok else 'GAGAL ' + info}")
    for src in SOURCES:
        try:
            if src["key"] == "n3x":
                posts, method = fetch_n3x(src["base"], 3)
            else:
                posts, method = fetch_source(src["key"], src["base"], 3)
            print(f"[{src['label']}] OK via {method}, contoh: {posts[0]['title'][:80]}")
        except Exception as e:  # noqa: BLE001
            print(f"[{src['label']}] GAGAL: {e}")
            traceback.print_exc(limit=1)


def main() -> None:
    ap = argparse.ArgumentParser(description="PaheDay notifier")
    ap.add_argument("--once", action="store_true", help="cek sekali lalu keluar")
    ap.add_argument("--init", action="store_true", help="baseline state tanpa kirim")
    ap.add_argument("--test", action="store_true", help="tes koneksi tanpa kirim/state")
    args = ap.parse_args()

    if args.test:
        do_test()
        return
    if args.init:
        do_init()
        return

    Config.validate()

    if args.once:
        n = check_once(send=True)
        print(f"Selesai, terkirim ke {n} chat (akumulasi).")
        return

    # mode loop
    print(f"PaheDay jalan. Interval {Config.CHECK_INTERVAL} dtk. Ctrl+C untuk berhenti.")
    if Config.SEND_STARTUP_MESSAGE:
        for cid in Config.CHAT_IDS:
            send_message(Config.BOT_TOKEN, cid, "<b>PaheDay notifier aktif</b> - memantau pahe.ink, dramaday.me & n3x.me")
    while True:
        try:
            n = check_once(send=True)
            print(f"[loop] putaran selesai, {n} terkirim.")
        except KeyboardInterrupt:
            print("Berhenti (Ctrl+C).")
            break
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        time.sleep(Config.CHECK_INTERVAL)


if __name__ == "__main__":
    main()
