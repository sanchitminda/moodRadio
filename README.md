# 🎵 SaM's Radio

A self-hosted **mood radio** with a local AI DJ. Pick a mood and a locally-running
LLM (Ollama or LM Studio) curates a playlist from **your own Navidrome library**.
The whole front page is a music player over a full-screen animated gradient that
shifts to match the mood. It's an installable **PWA** with **offline playback**.

- **Songs**: your existing **Navidrome** server (Subsonic API).
- **AI moods**: your **local** LLM via the OpenAI-compatible `/v1/chat/completions`
  endpoint — works with **Ollama** *or* **LM Studio** by changing one URL.
- **Lyrics**: free, keyless [LRCLIB](https://lrclib.net) (title/artist cleaned first
  so messy tags still match).
- **Genre**: your Navidrome tags, or optional **CLAP** audio classification that
  predicts genre from the sound itself (great for Bollywood/Hindi, where tags are
  poor) and feeds the mood scorer.
- **Extensible moods**: one config line adds a mood everywhere (prompt, DB, API, UI).
- **Runs in Docker**, single container. SQLite holds the index.

---

## How it works (two phases)

1. **Index / enrich** (a button you press once, then incrementally afterwards):
   scan every track from Navidrome → look up lyrics on LRCLIB → *(optional)* classify
   genre from audio with CLAP → have your local LLM score each track `0.0–1.0` against
   every mood → store it all in SQLite.
2. **Playback** (instant): pick a mood → the app reads precomputed scores, builds a
   weighted-random playlist (varied each time), and streams audio — proxied through
   the backend so your Navidrome credentials never reach the browser, with HTTP
   `Range` support for seeking.

```
Browser (player UI, animated gradient, PWA + offline cache)
   │
FastAPI (one container)
   ├─ /api/moods        → mood list (drives the pills)
   ├─ /api/radio        → weighted playlist from mood_scores
   ├─ /api/scores       → every track's per-mood + CLAP scores (Scores table)
   ├─ /api/stream/{id}  → Range-aware audio proxy → Navidrome
   ├─ /api/cover/{id}   → cover-art proxy → Navidrome
   ├─ /api/health       → Navidrome + LLM + CLAP status
   ├─ /api/version      → running build + effective config
   ├─ /api/index/*      → start / poll enrichment
   └─ background indexer → Navidrome + LRCLIB + local LLM (+ optional CLAP)
   └─ SQLite at /data,  CLAP model cache at /models
```

---

## Prerequisites

- **Docker** (Docker Desktop on Windows/Mac, or Docker Engine + Compose on Linux).
- A running **Navidrome** server and a user/password.
- A running **local LLM** exposing an OpenAI-compatible API:
  - **Ollama** on `:11434` — e.g. `ollama pull llama3.2 && ollama serve`
  - **LM Studio** on `:1234` — load a model and start its local server.

> **LM Studio note:** it requires `LLM_JSON_MODE=json_schema` (the default). Ollama
> also accepts `json_object`. The app auto-retries as plain text if a server rejects
> the chosen mode.
>
> **Networking:** if Navidrome/LLM run on the same host as Docker Desktop, the
> `host.docker.internal` defaults work. On Linux the bundled `extra_hosts` mapping
> makes that name resolve too. Or just use the machine's LAN IP.

---

## Setup & run

```bash
# 1. Configure
cp .env.sample .env         # a ready-to-edit sample with every setting
#    edit .env — set NAVIDROME_URL/USER/PASS and LLM_BASE_URL/LLM_MODEL

# 2. Build & start
docker compose up --build

# 3. Open the player
#    http://localhost:8080     (host port follows PORT in .env)
```

On first launch (empty library) an overlay prompts you to **build the library**.
Click **Start indexing** and watch the progress bar. For a large library this takes
a while (lyrics + LLM per track, and CLAP if enabled) — it's resumable and
incremental, so re-runs only touch new songs.

### Choosing where code and data live on your server

By default the app code is baked into the image and data goes to Docker named
volumes. To point Docker at **folders on your server** — update code without
rebuilding, keep the DB at a path you control — set these in `.env` (they drive the
bind mounts in `docker-compose.yml`):

```dotenv
APP_DIR=/srv/sams-radio/app       # app/ code, mounted into the container
DATA_DIR=/srv/sams-radio/data     # where moodradio.db is saved
MODEL_DIR=/srv/sams-radio/models  # where CLAP weights are cached
RELOAD=1                          # auto-reload the app on code edits
```

With `APP_DIR` set, the container runs the code at that path — ship a change by
**editing files there and `docker compose restart`** (or `RELOAD=1` for auto-reload).
You only need `docker compose up --build` when Python **dependencies** change.

---

## Configuration (`.env`)

| Variable | Default | Notes |
|---|---|---|
| `NAVIDROME_URL` | `http://host.docker.internal:4533` | Your Navidrome base URL |
| `NAVIDROME_USER` / `NAVIDROME_PASS` | — | Navidrome credentials |
| `LLM_BASE_URL` | `http://host.docker.internal:11434` | Ollama `:11434`, LM Studio `:1234` |
| `LLM_MODEL` | `llama3.2` | Model id loaded in your LLM server |
| `LLM_API_KEY` | `not-needed` | Sent as Bearer token if your server needs one |
| `LLM_JSON_MODE` | `json_schema` | `json_schema` (LM Studio), `json_object` (Ollama), or `text` |
| `MOODS` | *(11 built-in)* | `slug:Label,…` override; see **Moods** below |
| `INDEX_BATCH_SIZE` | `8` | Tracks per LLM scoring call |
| `LYRICS_USER_AGENT` | *(app string)* | LRCLIB etiquette — identify your app |
| `GENRE_MODEL_ENABLED` | `false` | CLAP audio genre prediction (needs `INSTALL_GENRE=true` build) |
| `GENRE_MODEL` | `laion/clap-htsat-unfused` | CLAP model |
| `GENRE_SEGMENT_SECONDS` | `20` | Seconds of audio analysed per track |
| `GENRE_LABELS` | *(Bollywood set)* | Comma-separated candidate labels |
| `GENRE_LABELS_FROM_LLM` | `false` | Let the LLM expand the CLAP label set at index start |
| `GENRE_LABELS_MAX` | `30` | Cap on the label list when expanded |
| `LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` (`DEBUG` traces every HTTP call) |
| `DB_PATH` | `/data/moodradio.db` | SQLite path (inside `/data`) |
| `PORT` | `8080` | Host port |
| `APP_DIR` / `DATA_DIR` / `MODEL_DIR` | *(volumes)* | Host paths for code / DB / model cache |
| `RELOAD` | *(off)* | `1` = uvicorn `--reload` |

### Switching Ollama ↔ LM Studio — just edit `.env`:

```dotenv
# Ollama
LLM_BASE_URL=http://host.docker.internal:11434
LLM_MODEL=llama3.2
LLM_JSON_MODE=json_object

# LM Studio
LLM_BASE_URL=http://host.docker.internal:1234
LLM_MODEL=your-loaded-model-id
LLM_JSON_MODE=json_schema
```

---

## Moods

The mood list is the single source of truth for the prompt, DB, API, and UI pills.
**11 are built in**: happy, sad, romantic, driving, long_drive_slow, party, workout,
chill, focus, nostalgic, devotional. Override or extend with `MOODS`:

```dotenv
MOODS=happy:Happy,sad:Sad,romantic:Romantic,driving:Driving,long_drive_slow:Long Drive · Slow,party:Party,workout:Workout,chill:Chill,focus:Focus,nostalgic:Nostalgic,devotional:Devotional
```

Restart and **re-index** so tracks are (re)scored for the new set. A slug without a
matching `.grad-<slug>` theme in [app/static/styles.css](app/static/styles.css) just
uses the default gradient; register a slug in `KNOWN_GRADIENTS`
([app/static/app.js](app/static/app.js)) + add a `.grad-<slug>` rule for a custom one.

---

## Genre prediction with CLAP (optional)

Navidrome tags are often missing or wrong (a Hindi song tagged "Blues"). Enable
**CLAP** to classify genre from the **audio** against a Bollywood-focused label set —
which then feeds the LLM's mood scoring and shows in the Scores table.

- Build with the deps (default in compose): `INSTALL_GENRE=true` pulls in ffmpeg +
  CPU **torch/transformers** ([requirements-genre.txt](requirements-genre.txt)).
- Turn it on: `GENRE_MODEL_ENABLED=true`.
- First index downloads the model (~1.7 GB) to the `/models` volume (once).
- It runs on **CPU** — adds ~1–3 s per track, so indexing is slower. `GENRE_LABELS`
  sets the candidate labels; `GENRE_LABELS_FROM_LLM=true` asks the LLM to expand that
  list (tailored to your moods + library) once at the start of a run.

Flow: `CLAP classifies audio → predicted genre → LLM mood scoring → mood scores`.
To build a small image without any of this, set `INSTALL_GENRE=false` and
`GENRE_MODEL_ENABLED=false`.

---

## UI features

- **Status pills** (top bar) for **Navidrome**, **LLM**, and **CLAP** — green =
  connected/enabled, red = unreachable/misconfigured, grey = disabled — plus a
  **Test connection** button. Hover a red/grey pill for the reason.
- **Scores** button → a searchable, sortable table of every track with its **genre**,
  **CLAP genre** (label + confidence, hover for the full distribution), and a
  color-coded score for **each mood**.
- **Rebuild library** → runs/monitors indexing.
- **Save offline** → caches the current playlist (see below).

---

## Install as an app + offline playback (PWA)

The player is a **Progressive Web App**: "Add to Home Screen" / "Install" makes it a
standalone app, and it's **mobile-friendly** (responsive layout, scrollable mood bar,
touch controls). **Save offline** caches the current playlist's audio + covers via a
service worker, so it keeps playing (with working seek) even if the server or internet
goes down; the last playlist is restored automatically when the backend is unreachable.

> ⚠️ **Service workers require a secure context.** Install + offline caching only work
> over **HTTPS** or `http://localhost` — **not** over a plain `http://<LAN-IP>:port`.
> The responsive UI works everywhere; for the PWA/offline features put the app behind
> HTTPS (e.g. a Caddy/Traefik reverse proxy) or use `http://localhost` on the host.

---

## Verifying

```bash
curl -s localhost:8080/api/version               # running build + effective config
curl -s localhost:8080/api/health                # {"navidrome":true,"llm":true,"genre":{...}}
curl -s localhost:8080/api/index/status          # watch "done" climb toward "total"
curl -s "localhost:8080/api/radio?mood=happy&limit=5"
```

Then in the UI: pick each mood → a queue loads, audio plays and seeks, auto-advances,
and the gradient crossfades to the mood's theme.

**Logs:** `docker compose logs -f`. Set `LOG_LEVEL=DEBUG` in `.env` for full HTTP
tracing (Navidrome/LLM requests, per-batch scoring, genre predictions).

---

## Notes & trade-offs

- **Initial indexing is slow** for big libraries (per-track lyrics + LLM, plus CLAP if
  on). Batching, caching, and incremental resumable runs keep it bounded.
- **Missing lyrics/genre is non-fatal**: scoring falls back to whatever is available.
- **Credentials stay server-side**: the browser only talks to this app, which proxies
  Navidrome for streams and cover art.
- **No paid/keyed services** are required.

## Project layout

```
music/
├─ docker-compose.yml       # single service; bind mounts + INSTALL_GENRE build arg
├─ Dockerfile               # python:3.12-slim (+ optional ffmpeg/torch for CLAP)
├─ requirements.txt         # fastapi, uvicorn, httpx, python-dotenv
├─ requirements-genre.txt   # optional: torch, transformers (CLAP)
├─ .env.sample / .env.example
├─ README.md
└─ app/
   ├─ main.py               # FastAPI app, routes, static hosting
   ├─ config.py             # env settings + MOODS + genre config
   ├─ logging_config.py     # stdout logging setup
   ├─ db.py                 # SQLite schema + queries
   ├─ navidrome.py          # Subsonic client (enumerate, stream, cover, audio bytes)
   ├─ lyrics.py             # LRCLIB client (+ title/artist cleaning)
   ├─ ai.py                 # LLM mood scorer + genre-label suggester (OpenAI-compatible)
   ├─ genre.py              # optional CLAP audio genre prediction
   ├─ indexer.py            # background enrichment job
   └─ static/               # index.html, styles.css, app.js, sw.js, manifest.json, icons
```
