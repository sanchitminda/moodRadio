"""FastAPI application: serves the player UI and the JSON/stream API."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .logging_config import configure_logging

configure_logging()

from . import ai, db, indexer, navidrome  # noqa: E402  (import after logging is set up)
from .config import settings  # noqa: E402

log = logging.getLogger("app.main")

STATIC_DIR = Path(__file__).parent / "static"

# Bump this whenever code changes so a running container can be identified.
APP_VERSION = "2026-08-29-clap-genre"


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    log.info("starting Mood Radio")
    log.info("navidrome_url=%s  llm_base_url=%s  llm_model=%s",
             settings.navidrome_url, settings.llm_base_url, settings.llm_model)
    log.info("db_path=%s  moods=%s", settings.db_path, ",".join(settings.mood_slugs))
    await db.init()
    log.info("database ready")
    yield


app = FastAPI(title="Mood Radio", lifespan=lifespan)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Log full tracebacks for anything that escapes a route."""
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"error": type(exc).__name__, "detail": str(exc)}, status_code=500)


# --- API --------------------------------------------------------------------

@app.get("/api/moods")
async def get_moods():
    return {"moods": [{"slug": slug, "label": label} for slug, label in settings.moods.items()]}


@app.get("/api/version")
async def version():
    """Identifies the running build + effective LLM config (for diagnostics)."""
    return {
        "version": APP_VERSION,
        "llm_model": settings.llm_model,
        "llm_json_mode": settings.llm_json_mode,
        "llm_base_url": settings.llm_base_url,
        "genre_model_enabled": settings.genre_model_enabled,
        "genre_model": settings.genre_model,
        "moods": settings.mood_slugs,
    }


@app.get("/api/health")
async def health():
    result = {"navidrome": False, "llm": False}
    try:
        result["navidrome"] = await navidrome.ping()
    except Exception as exc:  # noqa: BLE001
        result["navidrome_error"] = str(exc)
        log.warning("health: navidrome unreachable: %s", exc)
    try:
        result["llm"] = await ai.ping()
    except Exception as exc:  # noqa: BLE001
        result["llm_error"] = str(exc)
        log.warning("health: llm unreachable: %s", exc)
    ok = result["navidrome"] and result["llm"]
    log.info("health check: navidrome=%s llm=%s", result["navidrome"], result["llm"])
    return JSONResponse(result, status_code=200 if ok else 503)


@app.post("/api/index/start")
async def index_start():
    started = await indexer.start()
    log.info("index start requested -> started=%s", started)
    status = await db.get_state()
    return {"started": started, "state": status}


@app.get("/api/index/status")
async def index_status():
    state = await db.get_state()
    state["running"] = indexer.is_running()
    state["indexed"] = await db.count_tracks()
    state["pending"] = await db.count_pending()
    return state


@app.get("/api/radio")
async def radio(mood: str, limit: int = 50):
    if mood not in settings.moods:
        return JSONResponse({"error": f"unknown mood '{mood}'"}, status_code=400)
    limit = max(1, min(limit, 200))
    tracks = await db.radio(mood, limit)
    items = [
        {
            "id": t["id"],
            "title": t["title"],
            "artist": t["artist"],
            "album": t["album"],
            "duration": t["duration"],
            "score": round(t.get("mood_score", 0.0), 3),
            "stream_url": f"/api/stream/{t['id']}",
            "cover_url": f"/api/cover/{t['cover_art'] or t['id']}",
        }
        for t in tracks
    ]
    return {"mood": mood, "count": len(items), "tracks": items}


@app.get("/api/scores")
async def scores(limit: int = 2000, offset: int = 0):
    """Every track with its per-mood scores (for the Scores table)."""
    limit = max(1, min(limit, 5000))
    offset = max(0, offset)
    items = await db.all_scores(limit, offset)
    return {
        "moods": [{"slug": s, "label": settings.moods[s]} for s in settings.mood_slugs],
        "count": len(items),
        "offset": offset,
        "total": await db.count_tracks(),
        "tracks": items,
    }


def _forward_headers(upstream: httpx.Response) -> dict[str, str]:
    passthrough = {"content-type", "content-length", "content-range", "accept-ranges", "cache-control"}
    return {k: v for k, v in upstream.headers.items() if k.lower() in passthrough}


@app.get("/api/stream/{song_id}")
async def stream(song_id: str, request: Request):
    range_header = request.headers.get("range")
    try:
        upstream = await navidrome.stream(song_id, range_header)
    except Exception as exc:  # noqa: BLE001
        log.exception("stream failed for song_id=%s", song_id)
        return JSONResponse({"error": "stream_failed", "detail": str(exc)}, status_code=502)
    if upstream.status_code >= 400:
        log.warning("stream upstream HTTP %s for song_id=%s", upstream.status_code, song_id)

    async def body():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        body(),
        status_code=upstream.status_code,
        headers=_forward_headers(upstream),
    )


@app.get("/api/cover/{cover_id}")
async def cover(cover_id: str, size: int | None = None):
    try:
        upstream = await navidrome.cover_art(cover_id, size)
    except Exception as exc:  # noqa: BLE001
        log.exception("cover fetch failed for cover_id=%s", cover_id)
        return JSONResponse({"error": "cover_failed", "detail": str(exc)}, status_code=502)

    async def body():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()

    headers = _forward_headers(upstream)
    headers.setdefault("cache-control", "public, max-age=86400")
    return StreamingResponse(body(), status_code=upstream.status_code, headers=headers)


# --- Static frontend (mounted last so /api/* wins) --------------------------
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
