"""Subsonic API client for Navidrome.

Handles token authentication, full-library enumeration for indexing, and
range-aware streaming/cover-art proxying so the browser never sees the
Navidrome credentials.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from typing import Any, AsyncIterator

import httpx

from .config import settings

log = logging.getLogger(__name__)


class NavidromeError(Exception):
    pass


def _auth_params() -> dict[str, str]:
    """Build Subsonic token-auth params: t = md5(password + salt)."""
    salt = secrets.token_hex(8)
    token = hashlib.md5((settings.navidrome_pass + salt).encode("utf-8")).hexdigest()
    return {
        "u": settings.navidrome_user,
        "t": token,
        "s": salt,
        "v": settings.subsonic_version,
        "c": settings.subsonic_client,
        "f": "json",
    }


def _rest_url(endpoint: str) -> str:
    return f"{settings.navidrome_url.rstrip('/')}/rest/{endpoint}"


def _check(payload: dict[str, Any]) -> dict[str, Any]:
    resp = payload.get("subsonic-response", {})
    if resp.get("status") != "ok":
        err = resp.get("error", {})
        raise NavidromeError(err.get("message") or f"Subsonic error: {resp}")
    return resp


async def _get_json(client: httpx.AsyncClient, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    q = _auth_params()
    if params:
        q.update({k: v for k, v in params.items() if v is not None})
    url = _rest_url(endpoint)
    log.debug("navidrome GET %s params=%s", endpoint, {k: v for k, v in (params or {}).items()})
    try:
        r = await client.get(url, params=q, timeout=30.0)
    except httpx.HTTPError as exc:
        log.error("navidrome connection error on %s (%s): %s", endpoint, url, exc)
        raise
    if r.status_code >= 400:
        log.error("navidrome HTTP %s on %s: %s", r.status_code, endpoint, r.text[:300])
    r.raise_for_status()
    try:
        return _check(r.json())
    except NavidromeError as exc:
        log.error("navidrome API error on %s: %s", endpoint, exc)
        raise


async def ping() -> bool:
    async with httpx.AsyncClient() as client:
        await _get_json(client, "ping")
    log.debug("navidrome ping ok")
    return True


async def get_genres() -> list[str]:
    async with httpx.AsyncClient() as client:
        resp = await _get_json(client, "getGenres")
    genres = resp.get("genres", {}).get("genre", [])
    return [g.get("value", "") for g in genres if g.get("value")]


def _normalize_song(song: dict[str, Any], fallback_artist: str = "", album: str = "") -> dict[str, Any]:
    return {
        "id": str(song.get("id")),
        "title": song.get("title") or "Unknown",
        "artist": song.get("artist") or fallback_artist or "Unknown",
        "album": song.get("album") or album or "",
        "genre": song.get("genre") or "",
        "duration": int(song.get("duration") or 0),
        "cover_art": str(song.get("coverArt") or song.get("id") or ""),
    }


async def enumerate_library() -> AsyncIterator[dict[str, Any]]:
    """Walk the entire library: artists -> albums -> songs.

    Yields normalized song dicts. De-dupes by song id across the walk.
    """
    seen: set[str] = set()
    async with httpx.AsyncClient() as client:
        artists_resp = await _get_json(client, "getArtists")
        indexes = artists_resp.get("artists", {}).get("index", [])
        artists: list[dict[str, Any]] = []
        for idx in indexes:
            artists.extend(idx.get("artist", []))
        log.info("scan: %d artists to walk", len(artists))

        album_count = 0
        for artist in artists:
            artist_id = artist.get("id")
            artist_name = artist.get("name", "")
            if not artist_id:
                continue
            try:
                artist_resp = await _get_json(client, "getArtist", {"id": artist_id})
            except (httpx.HTTPError, NavidromeError) as exc:
                log.warning("scan: skipping artist %r (%s): %s", artist_name, artist_id, exc)
                continue
            albums = artist_resp.get("artist", {}).get("album", [])
            for album in albums:
                album_id = album.get("id")
                album_name = album.get("name", "")
                if not album_id:
                    continue
                try:
                    album_resp = await _get_json(client, "getAlbum", {"id": album_id})
                except (httpx.HTTPError, NavidromeError) as exc:
                    log.warning("scan: skipping album %r (%s): %s", album_name, album_id, exc)
                    continue
                album_count += 1
                songs = album_resp.get("album", {}).get("song", [])
                for song in songs:
                    norm = _normalize_song(song, artist_name, album_name)
                    if norm["id"] and norm["id"] not in seen:
                        seen.add(norm["id"])
                        yield norm
        log.info("scan: walked %d albums, yielded %d unique songs", album_count, len(seen))


async def stream(song_id: str, range_header: str | None = None) -> httpx.Response:
    """Open a streaming response for a song. Caller must close it.

    Forwards the client's Range header so seeking works.
    """
    client = httpx.AsyncClient(timeout=None)
    headers = {"Range": range_header} if range_header else {}
    q = _auth_params()
    q["id"] = song_id
    req = client.build_request("GET", _rest_url("stream"), params=q, headers=headers)
    resp = await client.send(req, stream=True)
    return resp


async def cover_art(cover_id: str, size: int | None = None) -> httpx.Response:
    """Open a streaming response for cover art. Caller must close it."""
    client = httpx.AsyncClient(timeout=30.0)
    q = _auth_params()
    q["id"] = cover_id
    if size:
        q["size"] = str(size)
    req = client.build_request("GET", _rest_url("getCoverArt"), params=q)
    resp = await client.send(req, stream=True)
    return resp


async def fetch_audio_bytes(song_id: str, max_bytes: int = 600_000) -> bytes:
    """Download (up to max_bytes of) a low-bitrate transcode of a song.

    Used for genre prediction — we only need the opening ~20s, so we request an
    mp3 transcode and stop reading once we have enough bytes. Robust even if the
    server can't transcode (ffmpeg decodes the original bytes downstream).
    """
    q = _auth_params()
    q["id"] = song_id
    q["format"] = "mp3"
    q["maxBitRate"] = "96"
    buf = bytearray()
    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream("GET", _rest_url("stream"), params=q) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes():
                buf.extend(chunk)
                if len(buf) >= max_bytes:
                    break
    return bytes(buf)
