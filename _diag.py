from datetime import datetime, timezone

from sources import fetch_wp_json

print("now UTC:", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))
for name, base in (("PAHE", "https://pahe.ink"), ("DRAMADAY", "https://dramaday.me")):
    posts, m = fetch_wp_json(base, 10)
    print(f"=== {name} via {m} ===")
    for p in posts[:10]:
        print(f"- [{p['modified']}] {p['title'][:70]}")
