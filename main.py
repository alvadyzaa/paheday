"""PaheDay — Telegram notifier untuk pahe.ink & dramaday.me.

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
    format_pahe,
    format_post,
    send_message,
    test_connection,
)
from sources import (
    fetch_post_detail,
    fetch_source,
    get_taxonomy_map,
    match_filter,
    parse_dramaday_detail,
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
    },
    {
        "key": "dramaday",
        "label": "Dramaday.me",
        "base": Config.DRAMADAY_URL,
        "include": Config.DRAMADAY_INCLUDE,
        "exclude": Config.DRAMADAY_EXCLUDE,
        "detail": True,  # ambil content + poster, kirim format kaya + foto
        "info": True,    # parse info drama (episode, network, dst)
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
        seen: dict = state.get(key, {})
        try:
            posts, method = fetch_source(key, src["base"], Config.PER_PAGE)
            print(f"[{label}] {len(posts)} post via {method}")
        except Exception as e:  # noqa: BLE001
            print(f"[{label}] GAGAL total: {e}")
            continue

        if detail and key not in tax_cache:
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

            if is_new or (is_update and Config.NOTIFY_UPDATES):
                info, cat_names = ({}, [])
                if detail:
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
            send_message(Config.BOT_TOKEN, cid, "<b>PaheDay notifier aktif</b> - memantau pahe.ink & dramaday.me")
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
