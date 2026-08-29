# 🎵 Mood Radio

A self-hosted **mood radio** with a local AI DJ. Pick a mood, and a locally-running
LLM (Ollama or LM Studio) curates a playlist from **your own Navidrome library**.
The whole front page is a music player, and the background is a full-screen animated
gradient that shifts to match the mood.

- **Songs**: your existing **Navidrome** server (Subsonic API).
- **AI**: your **local** LLM via the OpenAI-compatible `/v1/chat/completions` endpoint
  — works with **Ollama** *or* **LM Studio** by changing one URL.
- **Lyrics/genre**: free, keyless [LRCLIB](https://lrclib.net) + your Navidrome genre tags.
- **Runs in Docker**, single container. SQLite holds the index.
- **Moods are extensible** — one config line adds a new mood everywhere.

---

## How it works (two phases)

1. **Index / enrich** (a button you press once, then incrementally afterwards):
   scan every track from Navidrome → look up lyrics on LRCLIB → have your local
   LLM score each track `0.0–1.0` against every mood → store it all in SQLite.
2. **Playback** (instant): pick a mood → the app reads precomputed scores, builds a
   weighted-random playlist (varied each time), and streams audio — proxied through
   the backend so your Navidrome credentials never reach the browser, with HTTP
   `Range` support for seeking.

```
Browser (player UI + animated gradient)
   │
FastAPI (one container)
   ├─ /api/radio        → weighted playlist from mood_scores
   ├─ /api/stream/{id}  → Range-aware audio proxy → Navidrome
   ├─ /api/cover/{id}   → cover-art proxy → Navidrome
   ├─ /api/index/*      → start / poll enrichment
   └─ background indexer → Navidrome + LRCLIB + local LLM
   └─ SQLite at /data
```

---

## Prerequisites

- **Docker** (Docker Desktop on Windows/Mac, or Docker Engine + Compose on Linux).
- A running **Navidrome** server and a user/password.
- A running **local LLM** exposing an OpenAI-compatible API:
  - **Ollama** on `:11434` — e.g. `ollama pull llama3.2 && ollama serve`
  - **LM Studio** on `:1234` — load a model and start its local server.

> On **Windows/Mac Docker Desktop**, `host.docker.internal` already points at your
> host, so the defaults work if Navidrome/LLM run on the same machine. On **Linux**,
> the included `extra_hosts: host-gateway` mapping makes `host.docker.internal` work too.

---

## Setup & run

```bash
# 1. Configure
cp .env.example .env
#    then edit .env — set NAVIDROME_URL/USER/PASS and your LLM_BASE_URL/LLM_MODEL

# 2. Build & start
docker compose up --build

# 3. Open the player
#    http://localhost:8080
```

On first launch (empty library) a friendly overlay prompts you to **build the
library**. Click **Start indexing** and watch the progress bar. For a large
library this takes a while (one lyrics lookup + one LLM scoring per track) — it's
resumable and incremental, so you can close and reopen.

---

## Configuration (`.env`)

| Variable | Default | Notes |
|---|---|---|
| `NAVIDROME_URL` | `http://host.docker.internal:4533` | Your Navidrome base URL |
| `NAVIDROME_USER` / `NAVIDROME_PASS` | — | Navidrome credentials |
| `LLM_BASE_URL` | `http://host.docker.internal:11434` | Ollama `:11434`, LM Studio `:1234` |
| `LLM_MODEL` | `llama3.2` | Model name loaded in your LLM server |
| `LLM_API_KEY` | `not-needed` | Sent as Bearer token if your server requires one |
| `MOODS` | *(built-in)* | `slug:Label,…` override; see below |
| `INDEX_BATCH_SIZE` | `8` | Tracks per LLM scoring call |
| `LYRICS_USER_AGENT` | *(app string)* | LRCLIB etiquette — identify your app |
| `DB_PATH` | `/data/moodradio.db` | SQLite path (inside the `/data` volume) |
| `PORT` | `8080` | Host port |

### Switching Ollama ↔ LM Studio

No code change — just edit `.env`:

```dotenv
# Ollama
LLM_BASE_URL=http://host.docker.internal:11434
LLM_MODEL=llama3.2

# LM Studio
LLM_BASE_URL=http://host.docker.internal:1234
LLM_MODEL=your-loaded-model-name
```

Restart: `docker compose up -d`.

---

## Adding a mood

Moods are the single source of truth for the prompt, DB, API, and UI buttons.
To add one (e.g. **Focus**):

1. Set `MOODS` in `.env`, keeping the defaults and appending your new one:
   ```dotenv
   MOODS=happy:Happy,sad:Sad,romantic:Romantic,driving:Driving,long_drive_slow:Long Drive · Slow,focus:Focus
   ```
2. `docker compose up -d` to restart.
3. Click **Rebuild library** and re-index so existing tracks get scored for the new mood.

A new **Focus** pill appears automatically. If you didn't define a gradient theme
for the slug, it uses the default gradient. To give it a custom animated background,
add a `.grad-focus { … }` rule in [app/static/styles.css](app/static/styles.css)
and register the slug in the `KNOWN_GRADIENTS` set in [app/static/app.js](app/static/app.js).

---

## Verifying

```bash
# Both dependencies healthy?
curl -s localhost:8080/api/health        # {"navidrome":true,"llm":true}

# Indexing progress
curl -s localhost:8080/api/index/status  # watch "done" climb toward "total"

# A mood playlist
curl -s "localhost:8080/api/radio?mood=happy&limit=5"
```

Then in the UI: pick each mood → a queue loads, audio plays and seeks, auto-advances
to the next track, and the background gradient crossfades to the mood's theme.

---

## Notes & trade-offs

- **Initial indexing is inherently slow** for big libraries (per-track lyrics + LLM).
  Batching, lyrics caching, and incremental resumable runs keep it bounded — re-runs
  only touch newly added songs.
- **Missing lyrics is non-fatal**: scoring falls back to title/artist/genre.
- **Credentials stay server-side**: the browser only talks to this app, which proxies
  Navidrome for streams and cover art.
- **No paid/keyed services** are required.

## Project layout

```
music/
├─ docker-compose.yml
├─ Dockerfile
├─ requirements.txt
├─ .env.example
├─ README.md
└─ app/
   ├─ main.py        # FastAPI app, routes, static hosting
   ├─ config.py      # env settings + the MOODS source of truth
   ├─ db.py          # SQLite schema + queries
   ├─ navidrome.py   # Subsonic client (enumerate, stream, cover)
   ├─ lyrics.py      # LRCLIB client
   ├─ ai.py          # LLM mood scorer (OpenAI-compatible)
   ├─ indexer.py     # background enrichment job
   └─ static/        # index.html, styles.css, app.js  (the player)
```
