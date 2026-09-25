# PaheDay — Telegram Notifier untuk pahe.ink, dramaday.me & n3x.me

Bot Python sederhana yang memantau postingan **baru maupun update episode**
dari `pahe.ink`, `dramaday.me`, dan `n3x.me`, lalu mengirim notifikasi ke Telegram.

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

### 5. Anti-spam postingan lama (mis. postingan 2018 ikut ke-notify)

Penyebab:
1. bot memakai `orderby=modified`, jadi postingan lama yang ke-touch
   (edit typo/iklan/SEO → `modified` jadi hari ini) naik ke daftar teratas dan
   ikut terkirim sebagai BARU/UPDATE.
2. wp-json dan RSS memakai **skema ID beda** (numerik vs URL `?p=`) untuk
   postingan yang sama. Tiap flip metode (mis. saat wp-json kena Cloudflare
   403) membuat semua postingan terlihat BARU lagi.

Fix:
- bot hanya mengirim postingan **recent** berdasar tanggal **publish**
  (`date`), bukan `modified`. Postingan tua tetap ditandai sudah-dilihat
  agar tidak spam berulang (log `[skip-tua]`).
- state mencatat **dua kunci** (`ids` + `links`) per source. Postingan yang
  dikenali lewat link tapi ID-nya baru (flip metode) dicatat diam-diam
  tanpa notif (log `[skip-flip]`).
- poster n3x.me dicoba berurutan (cover → backdrop → teks) dengan host
  TMDB didahulukan, karena gambar lokal n3x.me sering 404.

Di `.env`:

```ini
# batas umur publish yang boleh memicu notif (hari). 0 = tanpa batas.
PAHE_MAX_AGE_DAYS=3        # pahe posting deras + pack Complete sering ke-touch
                           # ulang tanpa perubahan isi -> hanya rilisan fresh
DRAMADAY_MAX_AGE_DAYS=90   # dramaday = drama ongoing, 60-90 hari agar
                           # update episode 2-3 bulan setelah publish tetap masuk
# update pahe hampir selalu noise -> default false.
# update dramaday = episode baru -> default ikut NOTIFY_UPDATES (true).
PAHE_NOTIFY_UPDATES=false
DRAMADAY_NOTIFY_UPDATES=true
```

### 6. Slash commands (upcoming) via TVMaze

Chat ke bot: `/help`, `/upcoming_kdrama`, `/upcoming_series`,
`/upcoming_movies` (movies: segera, butuh TMDB API key gratis).
Jadwal diambil live dari TVMaze (tanpa API key, data real: judul, season/
episode, network, jam tayang) — hari ini + besok, tipe Berita/Talkshow/
Olahraga dibuang otomatis.

Catatan latensi: command dibaca tiap run. Di GitHub Actions (cron tiap
30 menit) balasan bisa telat sampai ~30 menit. Instant hanya kalau bot
jalan mode loop 24/7 (`python main.py` di PC/VPS). Hanya chat di
`TELEGRAM_CHAT_IDS` yang dilayani; chat asing diabaikan.

Agar command muncul di menu Telegram: chat @BotFather → `/setcommands` →
pilih bot → kirim:

```text
upcoming_kdrama - jadwal tayang Korea (hari ini + besok)
upcoming_series - jadwal tayang US (hari ini + besok)
upcoming_movies - segera (butuh TMDB API key)
help - bantuan
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

## Contoh notif n3x.me (foto + caption)

```text
POST BARU - N3x.me (Movie)
Antz (1998)
Genre: Animation, Comedy, Family
1998 | Rating 7.1 | 83 min
Link streaming/download
Diposting: Jumat, 25 Sep 2026 - 18:29 WIB
```

Sumber ketiga via public JSON API (`/api/posts`, pagination `?page&limit`).
API tidak punya konsep `modified`, jadi hanya rilisan BARU yang memicu notif
— otomatis kebal spam postingan lama. Cover film ikut sebagai foto.

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
| `sources.py` | fetcher pahe.ink & dramaday.me (wp-json + RSS fallback) + n3x.me (JSON API) |
| `notify.py` | kirim ke Telegram Bot API |
| `requirements.txt` | dependensi |

## Catatan Cloudflare dramaday.me

Selama pengetesan (Sep 2026) `wp-json` dan `/feed/` masih bisa diakses
dengan UA browser biasa. Kalau suatu saat kena challenge `403 / Just a moment`,
bot otomatis fallback ke RSS. Kalau keduanya diblokir, opsi lanjutan:

- pakai `curl_cffi` (impersonate chrome) — sudah disiapkan sebagai optional di `requirements.txt`
- atau pasang FlareSolverr / scraper API eksternal
