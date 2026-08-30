"""Application configuration, driven by environment variables.

The mood list is the single source of truth used by the LLM scorer, the
database, the API and the frontend. Add a mood here (or via the MOODS env
var) and re-index to score existing tracks against it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

# Load a local .env if present so `uvicorn app.main:app` works without Docker.
# In Docker the vars are already in the environment; load_dotenv (override=False)
# leaves those untouched, and there is no .env in the image, so this is a no-op.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ModuleNotFoundError:  # dotenv is optional; env vars still work without it
    pass



def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


# --- Mood definitions -------------------------------------------------------
# slug -> human-readable label. The slug is stored in the DB and used as the
# LLM score key; the label is shown in the UI.
DEFAULT_MOODS: dict[str, str] = {
    "happy": "Happy",
    "sad": "Sad",
    "romantic": "Romantic",
    "driving": "Driving",
    "long_drive_slow": "Long Drive · Slow",
    "party": "Party",
    "workout": "Workout",
    "chill": "Chill",
    "focus": "Focus",
    "nostalgic": "Nostalgic",
    "devotional": "Devotional",
}


def _parse_moods(raw: str) -> dict[str, str]:
    """Parse a MOODS override of the form "slug:Label,slug2:Label2".

    A bare "slug" (no colon) gets a title-cased label derived from the slug.
    """
    if not raw:
        return dict(DEFAULT_MOODS)
    moods: dict[str, str] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            slug, label = item.split(":", 1)
            slug, label = slug.strip(), label.strip()
        else:
            slug = item
            label = item.replace("_", " ").title()
        if slug:
            moods[slug] = label or slug
    return moods or dict(DEFAULT_MOODS)


def _bool(raw: str) -> bool:
    return raw.strip().lower() in ("1", "true", "yes", "on")


# --- Genre prediction (optional, CLAP zero-shot) ----------------------------
# Candidate labels the audio is matched against. Bollywood-focused by default;
# override with the GENRE_LABELS env var (comma-separated).
DEFAULT_GENRE_LABELS: list[str] = [
    "Bollywood film song",
    "romantic Hindi ballad",
    "Bollywood dance number",
    "item number",
    "retro classic Bollywood",
    "bhangra",
    "Punjabi pop",
    "ghazal",
    "qawwali",
    "sufi music",
    "Indian classical",
    "devotional bhajan",
    "Hindi hip hop / rap",
    "Hindi rock",
    "pop",
    "rock",
    "electronic dance music",
]


def _parse_labels(raw: str) -> list[str]:
    if not raw:
        return list(DEFAULT_GENRE_LABELS)
    labels = [item.strip() for item in raw.split(",") if item.strip()]
    return labels or list(DEFAULT_GENRE_LABELS)


@dataclass
class Settings:
    # Navidrome / Subsonic
    navidrome_url: str = field(default_factory=lambda: _env("NAVIDROME_URL", "http://host.docker.internal:4533"))
    navidrome_user: str = field(default_factory=lambda: _env("NAVIDROME_USER"))
    navidrome_pass: str = field(default_factory=lambda: _env("NAVIDROME_PASS"))
    subsonic_client: str = field(default_factory=lambda: _env("SUBSONIC_CLIENT", "mood-radio"))
    subsonic_version: str = field(default_factory=lambda: _env("SUBSONIC_VERSION", "1.16.1"))

    # Local LLM (OpenAI-compatible: Ollama :11434 or LM Studio :1234)
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", "http://host.docker.internal:11434"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "llama3.2"))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY", "not-needed"))
    # How to enforce JSON output. LM Studio requires "json_schema"; Ollama/OpenAI
    # accept "json_object". Use "text" to disable structured mode and rely on
    # tolerant parsing. Default is the most reliable structured mode.
    llm_json_mode: str = field(default_factory=lambda: _env("LLM_JSON_MODE", "json_schema"))

    # Lyrics
    lyrics_user_agent: str = field(
        default_factory=lambda: _env("LYRICS_USER_AGENT", "MoodRadio/0.1 (https://github.com/local/mood-radio)")
    )

    # Indexing
    index_batch_size: int = field(default_factory=lambda: int(_env("INDEX_BATCH_SIZE", "8") or 8))

    # Genre prediction (CLAP zero-shot audio classification) — optional
    genre_model_enabled: bool = field(default_factory=lambda: _bool(_env("GENRE_MODEL_ENABLED", "false")))
    genre_model: str = field(default_factory=lambda: _env("GENRE_MODEL", "laion/clap-htsat-unfused"))
    genre_segment_seconds: int = field(default_factory=lambda: int(_env("GENRE_SEGMENT_SECONDS", "20") or 20))
    genre_labels: list[str] = field(default_factory=lambda: _parse_labels(_env("GENRE_LABELS")))
    # Ask the LLM to expand the CLAP candidate-label list (tailored to the current
    # moods + library) before indexing. Off by default; uses genre_labels as seeds.
    genre_labels_from_llm: bool = field(default_factory=lambda: _bool(_env("GENRE_LABELS_FROM_LLM", "false")))
    genre_labels_max: int = field(default_factory=lambda: int(_env("GENRE_LABELS_MAX", "30") or 30))

    # Storage / server
    db_path: str = field(default_factory=lambda: _env("DB_PATH", "/data/moodradio.db"))
    port: int = field(default_factory=lambda: int(_env("PORT", "8080") or 8080))

    # Moods
    moods: dict[str, str] = field(default_factory=lambda: _parse_moods(_env("MOODS")))

    @property
    def mood_slugs(self) -> list[str]:
        return list(self.moods.keys())


settings = Settings()
