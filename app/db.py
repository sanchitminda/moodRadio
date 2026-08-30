"""SQLite persistence for tracks, mood scores and indexing state.

Uses the stdlib sqlite3 driver wrapped in asyncio.to_thread so the async
routes never block the event loop. The DB is small and single-writer
(the indexer), so this is plenty.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from typing import Any

from .config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL DEFAULT '',
    artist       TEXT NOT NULL DEFAULT '',
    album        TEXT NOT NULL DEFAULT '',
    genre        TEXT NOT NULL DEFAULT '',
    duration     INTEGER NOT NULL DEFAULT 0,
    cover_art    TEXT NOT NULL DEFAULT '',
    lyrics       TEXT,
    lyrics_source TEXT,
    genre_source TEXT,
    genre_scores TEXT,
    enriched_at  REAL,
    updated_at   REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS mood_scores (
    track_id TEXT NOT NULL,
    mood     TEXT NOT NULL,
    score    REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (track_id, mood),
    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_mood_scores_mood ON mood_scores(mood, score DESC);

CREATE TABLE IF NOT EXISTS index_state (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    status      TEXT NOT NULL DEFAULT 'idle',
    phase       TEXT NOT NULL DEFAULT '',
    total       INTEGER NOT NULL DEFAULT 0,
    done        INTEGER NOT NULL DEFAULT 0,
    started_at  REAL,
    updated_at  REAL,
    last_error  TEXT
);
"""


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(settings.db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(settings.db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _init_sync() -> None:
    conn = _connect()
    try:
        conn.executescript(_SCHEMA)
        conn.execute(
            "INSERT OR IGNORE INTO index_state (id, status) VALUES (1, 'idle')"
        )
        # Migrate older DBs that predate the genre columns.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tracks)")}
        for col in ("genre_source", "genre_scores"):
            if col not in cols:
                conn.execute(f"ALTER TABLE tracks ADD COLUMN {col} TEXT")
        conn.commit()
    finally:
        conn.close()


async def init() -> None:
    await asyncio.to_thread(_init_sync)


# --- Tracks -----------------------------------------------------------------

def _upsert_track_sync(track: dict[str, Any]) -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO tracks (id, title, artist, album, genre, duration, cover_art, updated_at)
            VALUES (:id, :title, :artist, :album, :genre, :duration, :cover_art, :updated_at)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title, artist=excluded.artist, album=excluded.album,
                genre=excluded.genre, duration=excluded.duration,
                cover_art=excluded.cover_art, updated_at=excluded.updated_at
            """,
            {**track, "updated_at": time.time()},
        )
        conn.commit()
    finally:
        conn.close()


async def upsert_track(track: dict[str, Any]) -> None:
    await asyncio.to_thread(_upsert_track_sync, track)


def _pending_tracks_sync(limit: int | None = None) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        sql = "SELECT * FROM tracks WHERE enriched_at IS NULL ORDER BY updated_at"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


async def pending_tracks(limit: int | None = None) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_pending_tracks_sync, limit)


def _count_sync(sql: str) -> int:
    conn = _connect()
    try:
        return int(conn.execute(sql).fetchone()[0])
    finally:
        conn.close()


async def count_tracks() -> int:
    return await asyncio.to_thread(_count_sync, "SELECT COUNT(*) FROM tracks")


async def count_pending() -> int:
    return await asyncio.to_thread(_count_sync, "SELECT COUNT(*) FROM tracks WHERE enriched_at IS NULL")


def _save_enrichment_sync(track_id: str, lyrics: str | None, lyrics_source: str | None,
                          scores: dict[str, float], genre: str | None = None,
                          genre_source: str | None = None,
                          genre_scores: dict[str, float] | None = None) -> None:
    conn = _connect()
    try:
        if genre is not None:
            conn.execute(
                """UPDATE tracks SET lyrics=?, lyrics_source=?, genre=?, genre_source=?,
                   genre_scores=?, enriched_at=? WHERE id=?""",
                (lyrics, lyrics_source, genre, genre_source,
                 json.dumps(genre_scores) if genre_scores else None, time.time(), track_id),
            )
        else:
            conn.execute(
                "UPDATE tracks SET lyrics=?, lyrics_source=?, enriched_at=? WHERE id=?",
                (lyrics, lyrics_source, time.time(), track_id),
            )
        conn.executemany(
            """
            INSERT INTO mood_scores (track_id, mood, score) VALUES (?, ?, ?)
            ON CONFLICT(track_id, mood) DO UPDATE SET score=excluded.score
            """,
            [(track_id, mood, float(score)) for mood, score in scores.items()],
        )
        conn.commit()
    finally:
        conn.close()


async def save_enrichment(track_id: str, lyrics: str | None, lyrics_source: str | None,
                          scores: dict[str, float], genre: str | None = None,
                          genre_source: str | None = None,
                          genre_scores: dict[str, float] | None = None) -> None:
    await asyncio.to_thread(_save_enrichment_sync, track_id, lyrics, lyrics_source, scores,
                            genre, genre_source, genre_scores)


def _radio_sync(mood: str, limit: int, pool_multiplier: int = 3) -> list[dict[str, Any]]:
    """Return tracks for a mood.

    Pull a larger pool of the top-scoring tracks, then randomly sample from it
    so the same mood doesn't produce an identical playlist every time.
    """
    import random

    conn = _connect()
    try:
        pool_size = max(limit * pool_multiplier, limit)
        rows = conn.execute(
            """
            SELECT t.*, ms.score AS mood_score
            FROM mood_scores ms
            JOIN tracks t ON t.id = ms.track_id
            WHERE ms.mood = ? AND ms.score > 0
            ORDER BY ms.score DESC
            LIMIT ?
            """,
            (mood, pool_size),
        ).fetchall()
        items = [dict(r) for r in rows]
        # Weighted sample favouring higher scores.
        if len(items) > limit:
            weights = [max(it["mood_score"], 0.01) for it in items]
            chosen = _weighted_sample_without_replacement(items, weights, limit)
        else:
            chosen = items
            random.shuffle(chosen)
        return chosen
    finally:
        conn.close()


def _weighted_sample_without_replacement(items: list[dict[str, Any]], weights: list[float],
                                         k: int) -> list[dict[str, Any]]:
    import random

    pool = list(zip(items, weights))
    chosen: list[dict[str, Any]] = []
    for _ in range(min(k, len(pool))):
        total = sum(w for _, w in pool)
        if total <= 0:
            break
        r = random.uniform(0, total)
        upto = 0.0
        for i, (item, w) in enumerate(pool):
            upto += w
            if upto >= r:
                chosen.append(item)
                pool.pop(i)
                break
    return chosen


async def radio(mood: str, limit: int) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_radio_sync, mood, limit)


def _all_scores_sync(limit: int, offset: int) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, title, artist, album, genre, genre_source, genre_scores FROM tracks ORDER BY artist, title LIMIT ? OFFSET ?",
            (int(limit), int(offset)),
        ).fetchall()
        tracks = [dict(r) for r in rows]
        if not tracks:
            return []
        for t in tracks:  # decode the stored CLAP label→probability JSON
            raw = t.pop("genre_scores", None)
            try:
                t["genre_scores"] = json.loads(raw) if raw else None
            except (TypeError, ValueError):
                t["genre_scores"] = None
        ids = [t["id"] for t in tracks]
        placeholders = ",".join("?" * len(ids))
        score_rows = conn.execute(
            f"SELECT track_id, mood, score FROM mood_scores WHERE track_id IN ({placeholders})",
            ids,
        ).fetchall()
        by_track: dict[str, dict[str, float]] = {}
        for r in score_rows:
            by_track.setdefault(r["track_id"], {})[r["mood"]] = r["score"]
        for t in tracks:
            t["scores"] = by_track.get(t["id"], {})
        return tracks
    finally:
        conn.close()


async def all_scores(limit: int, offset: int) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_all_scores_sync, limit, offset)


# --- Index state ------------------------------------------------------------

def _get_state_sync() -> dict[str, Any]:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM index_state WHERE id=1").fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


async def get_state() -> dict[str, Any]:
    return await asyncio.to_thread(_get_state_sync)


def _set_state_sync(**fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k}=?" for k in fields)
    conn = _connect()
    try:
        conn.execute(f"UPDATE index_state SET {cols} WHERE id=1", tuple(fields.values()))
        conn.commit()
    finally:
        conn.close()


async def set_state(**fields: Any) -> None:
    await asyncio.to_thread(_set_state_sync, **fields)
