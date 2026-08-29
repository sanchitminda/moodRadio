"""Lyrics lookup via LRCLIB (https://lrclib.net) — free, no API key.

Primary endpoint is /api/get (exact match on artist/title/album/duration).
On a miss we fall back to /api/search and take the best plain-lyrics result.
Missing lyrics is non-fatal; the caller scores on metadata alone.
"""
from __future__ import annotations

import html
import logging
import re
from typing import Any

import httpx

from .config import settings

log = logging.getLogger(__name__)

_BASE = "https://lrclib.net"
_TIMESTAMP_RE = re.compile(r"\[\d{1,2}:\d{2}(?:\.\d{1,3})?\]")

# Many libraries carry download-site cruft in titles/artists ("- www.Songs.PK",
# "[Alldesimp3.tk]", "- MP3Khan.Net") that breaks LRCLIB matching. Strip it.
_UNKNOWN_ARTIST = re.compile(r"unknown\s*artist", re.I)
_JUNK_GROUP = re.compile(
    r"[\(\[][^\)\]]*(?:www\.|https?:|\.tk|\.pk|\.net|\.com|songs\s*\.?\s*pk|mp3khan|alldesimp3|songspk)[^\)\]]*[\)\]]?",
    re.I,
)
_JUNK_TRAIL = re.compile(
    r"\s*[-–]\s*(?:www\.|https?://|\S*(?:songspk|mp3khan|alldesimp3|songs\.?pk)\S*|\S+\.\S+)\s*$",
    re.I,
)


def _clean(text: str) -> str:
    """Unescape HTML entities and strip download-site junk from a title/artist."""
    if not text:
        return ""
    t = html.unescape(text)
    t = _JUNK_GROUP.sub(" ", t)
    prev = None
    while prev != t:  # peel repeated trailing " - site.tld" tails
        prev = t
        t = _JUNK_TRAIL.sub("", t).strip()
    return re.sub(r"\s{2,}", " ", t).strip(" -–[]")


def _strip_timestamps(synced: str) -> str:
    lines = []
    for line in synced.splitlines():
        cleaned = _TIMESTAMP_RE.sub("", line).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _pick_lyrics(record: dict[str, Any]) -> str | None:
    if record.get("instrumental"):
        return ""
    plain = (record.get("plainLyrics") or "").strip()
    if plain:
        return plain
    synced = (record.get("syncedLyrics") or "").strip()
    if synced:
        return _strip_timestamps(synced)
    return None


async def fetch_lyrics(client: httpx.AsyncClient, *, title: str, artist: str,
                       album: str = "", duration: int = 0) -> tuple[str | None, str | None]:
    """Return (lyrics, source) where source is 'lrclib' or None."""
    title = _clean(title)
    artist = "" if _UNKNOWN_ARTIST.search(artist or "") else _clean(artist)
    album = _clean(album)
    headers = {"User-Agent": settings.lyrics_user_agent}

    # Exact-match endpoint needs a real artist; skip it if we only have a title.
    if artist:
        params: dict[str, Any] = {"track_name": title, "artist_name": artist}
        if album:
            params["album_name"] = album
        if duration:
            params["duration"] = duration
        try:
            r = await client.get(f"{_BASE}/api/get", params=params, headers=headers, timeout=15.0)
            if r.status_code == 200:
                lyrics = _pick_lyrics(r.json())
                if lyrics is not None:
                    return lyrics, "lrclib"
        except (httpx.HTTPError, ValueError):
            pass

    # Fallback: search (title-only when the artist is unknown) and take the
    # first usable result.
    search: dict[str, Any] = {"track_name": title}
    if artist:
        search["artist_name"] = artist
    try:
        r = await client.get(f"{_BASE}/api/search", params=search, headers=headers, timeout=15.0)
        if r.status_code == 200:
            for record in r.json() or []:
                lyrics = _pick_lyrics(record)
                if lyrics:
                    return lyrics, "lrclib"
    except (httpx.HTTPError, ValueError):
        pass

    log.debug("lyrics miss for %r / %r", title, artist or "(unknown)")
    return None, None
