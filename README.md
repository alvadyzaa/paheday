# PaheDay — Telegram Notifier untuk pahe.ink & dramaday.me

Bot Python sederhana yang memantau postingan **baru maupun update episode**
dari `pahe.ink` dan `dramaday.me`, lalu mengirim notifikasi ke Telegram.

## Kenapa bukan RSS saja?

Kedua situs adalah WordPress, tapi:

- `dramaday.me` **mengupdate post lama** saat episode baru rilis
  (contoh: `date=2026-08-24` tapi `modified=2026-09-23`).
  RSS (`/feed/`) hanya menampilkan `date` → update episode **lolos**.
- Solusi: pakai **WP REST API** dengan `orderby=modified&order=desc`
  sehingga update episode ikut terdeteksi.

Bot memakai strategi berlapis (tahan Cloudflare):

1. `wp-json/wp/v2/posts?orderby=modified` (utama)
2. Fallback ke RSS `/feed/` kalau wp-json kena 403 / challenge Cloudflare
3. Header browser-like + retry + backoff

## Cara pakai

### 1. Buat bot Telegram

1. Chat ke [@BotFather](https://t.me/BotFather) → `/newbot` → dapat **BOT_TOKEN**.
2. Kirim pesan apa saja ke bot kamu, lalu buka:
   `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates`
   catat `chat.id` → ini **CHAT_ID**.
   - Untuk channel/grup: invite bot sebagai admin, kirim pesan, cek getUpdates lagi.
   - Bisa multi-ID dipisah koma: `12345,-100xxxx`.

### 2. Install & konfigurasi

```powershell
cd D:\CODING\paheday
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# edit .env isi BOT_TOKEN & CHAT_ID
```

### 3. Jalankan

```powershell
# test koneksi telegram + fetch sekali (tanpa simpan state)
python main.py --test

# cek sekali & kirim notif untuk item baru (disarankan pertama kali: --init dulu)
python main.py --init    # tandai semua sebagai sudah-dilihat, tanpa kirim
python main.py --once    # cek sekali & kirim kalau ada yang baru

# mode loop (polling tiap CHECK_INTERVAL_SECONDS)
python main.py
```

### 4. Filter (opsional, karena pahe.ink update-nya deras)

Di `.env`:

```ini
# kosong = semua dikirim. Contoh hanya anime/k-drama:
PAHE_INCLUDE=anime,k-drama,complete,season
PAHE_EXCLUDE=
DRAMADAY_INCLUDE=
DRAMADAY_EXCLUDE=OST
# NOTIFY_UPDATES=true → update episode ikut dikirim (disarankan true untuk dramaday)
NOTIFY_UPDATES=true
```

## Contoh notif dramaday (foto + caption)

```text
UPDATE EPISODE - Dramaday.me
New Recruit (S4)
Episode 20 dari 24 total
Ongoing | 2026 | ENA
Monday & Tuesday 22:00 KST
Genre: Military, Comedy
Link download
Diposting: Senin, 24 Agu 2026 - 22:55 WIB
Diupdate: Rabu, 23 Sep 2026 - 04:58 WIB
```
Dilengkapi poster drama sebagai foto. Kalau foto gagal terkirim,
otomatis fallback ke pesan teks + sinopsis.

## Contoh notif pahe (foto + caption)

```text
POST BARU - Pahe.ink
Moneyball (2011) REMASTERED BluRay 480p, 720p & 1080p
Genre: Biography, Drama, Sport
2011 | 1080p
Link download
```
Poster film ikut sebagai foto, tahun/kualitas/codec dibaca dari tag.

## Jalan di GitHub Actions (gratis, tanpa VPS)

Workflow sudah tersedia di `.github/workflows/check.yml` (jalan tiap 30 menit).

1. Buat repo GitHub baru, push folder ini:
   ```powershell
   cd D:\CODING\paheday
   git init; git add -A; git commit -m "PaheDay notifier"
   git branch -M main
   git remote add origin https://github.com/USERNAME/paheday.git
   git push -u origin main
   ```
   (`.env` dan `state.json` otomatis tidak ikut karena `.gitignore`.)
2. Di repo → **Settings → Secrets and variables → Actions → New repository secret**:
   - `TELEGRAM_BOT_TOKEN` = token dari BotFather
   - `TELEGRAM_CHAT_IDS` = user id kamu (`1955401353`)
3. Buka tab **Actions** → enable workflows kalau diminta → **Run workflow**
   manual sekali untuk tes. Run pertama hanya baseline (tidak kirim),
   run berikutnya baru kirim notif.
4. **Penting:** matikan bot di PC (`Stop-Process -Name python`) supaya
   tidak double notif — state PC dan state Actions terpisah.

Catatan:
- Jadwal cron GitHub bisa delay beberapa menit dan hanya jalan di branch default.
- Interval default `*/30` agar aman dari kuota gratis (2000 mnt/bln).
  Bisa diturunkan ke `*/15` di file workflow kalau mau lebih cepat.

## Deploy 24/7

- **VPS / PC rumah:** `python main.py` + Task Scheduler / systemd.
- **Docker:** `docker build -t paheday . ; docker run -d --env-file .env paheday`
- **GitHub Actions (gratis, tanpa VPS):** lihat `.github/workflows/check.yml`
  — jalan tiap 15 menit, state disimpan via Actions cache/artifacts.

## File

| File | Fungsi |
|---|---|
| `main.py` | entrypoint: polling loop, CLI flags |
| `config.py` | baca `.env` |
| `sources.py` | fetcher pahe.ink & dramaday.me (wp-json + RSS fallback) |
| `storage.py` | simpan `state.json` (id → modified) |
| `notify.py` | kirim ke Telegram Bot API |
| `requirements.txt` | dependensi |

## Catatan Cloudflare dramaday.me

Selama pengetesan (Sep 2026) `wp-json` dan `/feed/` masih bisa diakses
dengan UA browser biasa. Kalau suatu saat kena challenge `403 / Just a moment`,
bot otomatis fallback ke RSS. Kalau keduanya diblokir, opsi lanjutan:

- pakai `curl_cffi` (impersonate chrome) — sudah disiapkan sebagai optional di `requirements.txt`
- atau pasang FlareSolverr / scraper API eksternal
