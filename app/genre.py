"""Optional CLAP-based zero-shot genre prediction.

Uses LAION-CLAP (via transformers) to match a track's audio against a list of
text labels (Bollywood-focused by default) — so it works for Hindi/Bollywood
music that Western-taxonomy classifiers mislabel. Everything here is lazily
imported and gated behind GENRE_MODEL_ENABLED, so the base app runs fine
without torch/transformers installed.

Audio is decoded with ffmpeg (robust to any codec and to truncated input),
so no torchaudio/librosa/soundfile is required.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess

from .config import settings

log = logging.getLogger(__name__)

_model = None
_processor = None
_load_failed = False


def is_enabled() -> bool:
    return settings.genre_model_enabled


def _lazy_load() -> bool:
    """Load the CLAP model once. Returns True if usable."""
    global _model, _processor, _load_failed
    if _model is not None:
        return True
    if _load_failed:
        return False
    try:
        from transformers import ClapModel, ClapProcessor  # heavy import
    except ModuleNotFoundError as exc:
        log.error("genre prediction enabled but transformers/torch not installed: %s", exc)
        _load_failed = True
        return False
    try:
        name = settings.genre_model
        log.info("loading CLAP model %s (first run downloads weights)…", name)
        _processor = ClapProcessor.from_pretrained(name)
        _model = ClapModel.from_pretrained(name)
        _model.eval()
        log.info("CLAP model ready")
        return True
    except Exception as exc:  # noqa: BLE001
        log.exception("failed to load CLAP model: %s", exc)
        _load_failed = True
        return False


def _decode_pcm(raw: bytes, seconds: int):
    """Decode arbitrary audio bytes to a mono 48 kHz float32 numpy array."""
    import numpy as np

    try:
        proc = subprocess.run(
            ["ffmpeg", "-loglevel", "quiet", "-i", "pipe:0",
             "-f", "f32le", "-ac", "1", "-ar", "48000", "-t", str(seconds), "pipe:1"],
            input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        log.error("ffmpeg not found in image; cannot decode audio for genre prediction")
        return None
    if not proc.stdout:
        return None
    arr = np.frombuffer(proc.stdout, dtype=np.float32)
    return arr if arr.size else None


def _predict_sync(raw: bytes) -> tuple[str | None, dict | None]:
    if not _lazy_load():
        return None, None
    import torch

    audio = _decode_pcm(raw, settings.genre_segment_seconds)
    if audio is None or audio.size < 48000:  # need at least ~1s
        return None, None

    labels = settings.genre_labels
    inputs = _processor(text=labels, audios=[audio], sampling_rate=48000,
                        return_tensors="pt", padding=True)
    with torch.no_grad():
        out = _model(**inputs)
    probs = out.logits_per_audio.softmax(dim=-1)[0].tolist()
    scored = sorted(zip(labels, probs), key=lambda x: x[1], reverse=True)
    top_label, top_prob = scored[0]
    return top_label, {label: round(p, 4) for label, p in scored}


async def predict(raw_audio: bytes | None) -> tuple[str | None, dict | None]:
    """Predict a genre label from raw audio bytes. (None, None) on any failure."""
    if not raw_audio or not is_enabled():
        return None, None
    try:
        return await asyncio.to_thread(_predict_sync, raw_audio)
    except Exception as exc:  # noqa: BLE001
        log.error("genre prediction failed (%s): %s", type(exc).__name__, exc)
        return None, None
