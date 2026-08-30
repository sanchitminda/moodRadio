"""Background indexing job: the "initial run" that enriches the library.

Two phases:
  1. scan   - enumerate every track from Navidrome and upsert into the DB.
  2. enrich - for each not-yet-enriched track, fetch lyrics (LRCLIB) and
              score it against every mood via the local LLM, then persist.

The job is incremental (already-enriched tracks are skipped), resumable, and
guarded against concurrent runs. Progress is written to index_state and
polled by the frontend.
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

from . import ai, db, genre, lyrics, navidrome
from .config import settings

log = logging.getLogger(__name__)

_lock = asyncio.Lock()
_running = False


def is_running() -> bool:
    return _running


async def _scan() -> None:
    log.info("phase=scan starting library enumeration")
    await db.set_state(phase="scan", status="running", last_error=None)
    count = 0
    async for song in navidrome.enumerate_library():
        await db.upsert_track(song)
        count += 1
        if count % 25 == 0:
            await db.set_state(total=count)
            log.info("scan: upserted %d tracks so far", count)
    await db.set_state(total=count)
    log.info("phase=scan done, %d tracks in library", count)


async def _enrich() -> None:
    await db.set_state(phase="enrich")
    total_pending = await db.count_pending()
    total_tracks = await db.count_tracks()
    done_already = total_tracks - total_pending
    await db.set_state(total=total_tracks, done=done_already)
    log.info("phase=enrich starting: %d pending of %d total (batch size %d)",
             total_pending, total_tracks, settings.index_batch_size)

    processed = done_already
    batch_no = 0
    async with httpx.AsyncClient() as client:
        # If enabled, ask the LLM to expand the CLAP candidate labels once, up
        # front, tailored to the current moods + library. Falls back to the
        # configured seed labels on any failure.
        if genre.is_enabled() and settings.genre_labels_from_llm:
            labels = await ai.suggest_genre_labels(
                client, moods=settings.moods,
                seed_labels=settings.genre_labels, limit=settings.genre_labels_max,
            )
            genre.set_labels(labels)
            log.info("CLAP labels for this run: %s", ", ".join(labels))

        while True:
            batch = await db.pending_tracks(limit=settings.index_batch_size)
            if not batch:
                break
            batch_no += 1

            # Fetch lyrics for the batch (concurrently, politely bounded).
            async def _with_lyrics(track: dict) -> dict:
                text, source = await lyrics.fetch_lyrics(
                    client,
                    title=track["title"],
                    artist=track["artist"],
                    album=track.get("album", ""),
                    duration=track.get("duration", 0),
                )
                return {**track, "lyrics": text, "lyrics_source": source}

            enriched = await asyncio.gather(*[_with_lyrics(t) for t in batch])
            with_lyrics = sum(1 for t in enriched if t.get("lyrics"))
            log.info("enrich batch #%d: %d tracks (%d with lyrics)",
                     batch_no, len(enriched), with_lyrics)

            # Predict genre from audio (CLAP), sequentially to bound CPU/memory.
            # This overrides the (often junk) Navidrome genre and feeds the
            # mood-scoring prompt below.
            if genre.is_enabled():
                for track in enriched:
                    try:
                        raw = await navidrome.fetch_audio_bytes(track["id"])
                        label, gscores = await genre.predict(raw)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("genre fetch/predict failed for %s: %s", track["id"], exc)
                        label, gscores = None, None
                    if label:
                        log.info("genre: %r -> %s", track.get("title", "")[:40], label)
                        track["genre"] = label
                        track["genre_source"] = "clap"
                        track["genre_scores"] = gscores
                    else:
                        track.setdefault("genre_source", "navidrome" if track.get("genre") else None)

            # Score the whole batch in one LLM call.
            scores = await ai.score_batch(client, enriched)

            for track in enriched:
                track_scores = scores.get(track["id"], {s: 0.0 for s in settings.mood_slugs})
                await db.save_enrichment(
                    track["id"], track.get("lyrics"), track.get("lyrics_source"), track_scores,
                    genre=track.get("genre") if track.get("genre_source") == "clap" else None,
                    genre_source=track.get("genre_source"),
                    genre_scores=track.get("genre_scores"),
                )
                processed += 1
            await db.set_state(done=processed)
            log.info("enrich progress: %d/%d", processed, total_tracks)
    log.info("phase=enrich done, %d tracks enriched", processed)


async def _run() -> None:
    global _running
    _running = True
    try:
        await db.set_state(status="running", started_at=time.time(), done=0, last_error=None)
        await _scan()
        await _enrich()
        await db.set_state(status="done", phase="done")
        log.info("indexing complete")
    except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
        log.exception("indexing failed")
        await db.set_state(status="error", last_error=str(exc))
    finally:
        _running = False


async def start() -> bool:
    """Kick off indexing if not already running. Returns True if started."""
    if _lock.locked() or _running:
        return False
    async with _lock:
        if _running:
            return False
        asyncio.create_task(_run())
        return True
