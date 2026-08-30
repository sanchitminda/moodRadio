"""Mood scoring via a local, OpenAI-compatible LLM.

Works unchanged against Ollama (:11434) and LM Studio (:1234) because both
expose POST /v1/chat/completions. Tracks are scored in batches to minimize
round-trips; the prompt and the expected JSON keys are generated from the
configured mood list.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .config import settings

log = logging.getLogger(__name__)

_LYRICS_EXCERPT_CHARS = 1200


def _mood_reference() -> str:
    return "\n".join(f'  - "{slug}": {label}' for slug, label in settings.moods.items())


def _build_track_block(track: dict[str, Any]) -> dict[str, Any]:
    lyrics = (track.get("lyrics") or "").strip()
    if len(lyrics) > _LYRICS_EXCERPT_CHARS:
        lyrics = lyrics[:_LYRICS_EXCERPT_CHARS] + " …"
    return {
        "id": track["id"],
        "title": track.get("title", ""),
        "artist": track.get("artist", ""),
        "genre": track.get("genre", ""),
        "lyrics_excerpt": lyrics or "(no lyrics available)",
    }


def _build_messages(batch: list[dict[str, Any]]) -> list[dict[str, str]]:
    slugs = settings.mood_slugs
    tracks = [_build_track_block(t) for t in batch]
    system = (
        "You are a music-mood analyst. For each song, judge how well it fits each mood "
        "using its genre, artist, title and lyrics. Consider tempo, energy and emotional tone. "
        "Return a score from 0.0 (does not fit) to 1.0 (fits perfectly) for every mood."
    )
    user = (
        "Moods (use these exact keys):\n"
        f"{_mood_reference()}\n\n"
        "Songs:\n"
        f"{json.dumps(tracks, ensure_ascii=False, indent=2)}\n\n"
        "Respond with a JSON object of this exact shape and nothing else:\n"
        '{ "results": [ { "id": "<song id>", "scores": { '
        + ", ".join(f'"{s}": 0.0' for s in slugs)
        + " } } ] }"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _clamp(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _parse_response(content: str) -> dict[str, dict[str, float]]:
    """Return {track_id: {mood: score}}. Tolerant of extra prose around JSON."""
    text = content.strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log.warning("LLM response was not valid JSON; head=%r", content[:200])
        return {}

    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        log.warning("LLM response had no 'results' list; head=%r", content[:200])
        return {}

    out: dict[str, dict[str, float]] = {}
    for entry in results:
        if not isinstance(entry, dict):
            continue
        tid = str(entry.get("id", ""))
        raw_scores = entry.get("scores", {})
        if not tid or not isinstance(raw_scores, dict):
            continue
        out[tid] = {slug: _clamp(raw_scores.get(slug, 0.0)) for slug in settings.mood_slugs}
    return out


def _zero_scores() -> dict[str, float]:
    return {slug: 0.0 for slug in settings.mood_slugs}


def _json_schema() -> dict[str, Any]:
    """A JSON schema for the expected results, generated from the mood list."""
    slugs = settings.mood_slugs
    return {
        "name": "mood_scores",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "scores": {
                                "type": "object",
                                "properties": {s: {"type": "number"} for s in slugs},
                                "required": list(slugs),
                                "additionalProperties": False,
                            },
                        },
                        "required": ["id", "scores"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["results"],
            "additionalProperties": False,
        },
    }


def _response_format() -> dict[str, Any] | None:
    """Structured-output directive, per provider. None = plain text."""
    mode = (settings.llm_json_mode or "json_schema").lower()
    if mode == "json_schema":  # LM Studio (and OpenAI)
        return {"type": "json_schema", "json_schema": _json_schema()}
    if mode == "json_object":  # Ollama / OpenAI
        return {"type": "json_object"}
    return None  # "text" / "off" -> rely on tolerant parsing


async def score_batch(client: httpx.AsyncClient, batch: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Score a batch of tracks. Missing/invalid entries default to zero scores."""
    url = f"{settings.llm_base_url.rstrip('/')}/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"

    messages = _build_messages(batch)

    def _payload(response_format: dict[str, Any] | None) -> dict[str, Any]:
        p: dict[str, Any] = {
            "model": settings.llm_model,
            "messages": messages,
            "temperature": 0.2,
            "stream": False,
        }
        if response_format is not None:
            p["response_format"] = response_format
        return p

    scores: dict[str, dict[str, float]] = {}
    rf = _response_format()
    log.info("scoring batch of %d track(s) with model=%s (json_mode=%s)",
             len(batch), settings.llm_model, settings.llm_json_mode)
    try:
        r = await client.post(url, json=_payload(rf), headers=headers, timeout=180.0)
        # Auto-heal: some servers reject a given response_format. Retry as text.
        if r.status_code == 400 and rf is not None and "response_format" in r.text.lower():
            log.warning("server rejected response_format=%s (%s); retrying without structured output",
                        settings.llm_json_mode, r.text[:150])
            r = await client.post(url, json=_payload(None), headers=headers, timeout=180.0)
        if r.status_code >= 400:
            log.error("LLM scoring HTTP %s from %s: %s", r.status_code, url, r.text[:500])
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        scores = _parse_response(content)
        if scores:
            log.info("scored %d/%d track(s)", len(scores), len(batch))
        else:
            log.warning("LLM returned no usable scores for batch of %d", len(batch))
    except httpx.HTTPError as exc:
        log.error("LLM scoring request failed (%s): %s", type(exc).__name__, exc)
    except (KeyError, ValueError, IndexError) as exc:
        log.error("LLM scoring response malformed (%s): %s", type(exc).__name__, exc)

    # Guarantee every track in the batch gets an entry.
    for track in batch:
        scores.setdefault(track["id"], _zero_scores())
    return scores


async def ping() -> bool:
    """Check the LLM endpoint is reachable (lists models)."""
    url = f"{settings.llm_base_url.rstrip('/')}/v1/models"
    headers = {}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=headers, timeout=10.0)
        if r.status_code >= 400:
            log.warning("LLM ping HTTP %s from %s: %s", r.status_code, url, r.text[:300])
        r.raise_for_status()
    log.debug("llm ping ok (%s)", url)
    return True


async def suggest_genre_labels(client: httpx.AsyncClient, *, moods: dict[str, str],
                               seed_labels: list[str], limit: int) -> list[str]:
    """Ask the LLM for a genre-label set for CLAP, tailored to the moods/library.

    Returns the seed labels merged with the LLM's suggestions (deduped, capped at
    `limit`). On any failure, returns the seed labels unchanged.
    """
    url = f"{settings.llm_base_url.rstrip('/')}/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"

    mood_list = ", ".join(moods.values())
    system = (
        "You are a music taxonomy expert. Produce concise genre / sub-genre labels "
        "suitable for zero-shot audio classification (CLAP). Favour specific, "
        "widely-understood styles. The library is heavily Indian/Bollywood, so "
        "include relevant Indian styles as well as mainstream ones."
    )
    user = (
        f"The app sorts songs into these moods: {mood_list}.\n"
        f"Here are seed genre labels: {', '.join(seed_labels)}.\n"
        f"Return up to {limit} genre labels total (keep the useful seeds, add more "
        "that would help distinguish these moods and cover this library). "
        'Respond as JSON: {"labels": ["label1", "label2", ...]}'
    )
    schema = {
        "name": "genre_labels", "strict": True,
        "schema": {
            "type": "object",
            "properties": {"labels": {"type": "array", "items": {"type": "string"}}},
            "required": ["labels"], "additionalProperties": False,
        },
    }
    payload: dict[str, Any] = {
        "model": settings.llm_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.4,
        "stream": False,
    }
    rf = _response_format()
    if rf and rf.get("type") == "json_schema":
        payload["response_format"] = {"type": "json_schema", "json_schema": schema}
    elif rf:
        payload["response_format"] = rf

    try:
        r = await client.post(url, json=payload, headers=headers, timeout=120.0)
        if r.status_code >= 400:
            log.error("genre-label suggestion HTTP %s: %s", r.status_code, r.text[:300])
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        text = content[content.find("{"): content.rfind("}") + 1] or content
        data = json.loads(text)
        suggested = data.get("labels") if isinstance(data, dict) else None
        if not isinstance(suggested, list):
            raise ValueError("no 'labels' array")
    except (httpx.HTTPError, KeyError, ValueError, IndexError) as exc:
        log.error("genre-label suggestion failed (%s): %s; using seed labels", type(exc).__name__, exc)
        return list(seed_labels)

    # Merge seeds first (preserve order), then suggestions; dedupe case-insensitively.
    merged: list[str] = []
    seen: set[str] = set()
    for label in [*seed_labels, *suggested]:
        if not isinstance(label, str):
            continue
        label = label.strip()
        key = label.lower()
        if label and key not in seen:
            seen.add(key)
            merged.append(label)
        if len(merged) >= limit:
            break
    log.info("genre labels: %d seed -> %d after LLM expansion", len(seed_labels), len(merged))
    return merged
