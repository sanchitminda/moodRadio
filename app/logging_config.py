"""Central logging setup.

One place configures logging for the whole app so container logs are readable
and every module can just do `logging.getLogger(__name__)`. Level is driven by
the LOG_LEVEL env var (default INFO; set DEBUG for verbose HTTP tracing).
"""
from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False


def configure_logging() -> None:
    """Install a stdout handler on the root logger. Idempotent."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        level=level,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        force=True,  # win over any handler uvicorn/others already installed
    )

    # Our own namespace at the requested level; keep httpx's per-request lines
    # visible (they help diagnose Navidrome/LLM connectivity) but quiet the
    # noisier httpcore internals unless DEBUG was explicitly asked for.
    logging.getLogger("app").setLevel(level)
    logging.getLogger("httpx").setLevel(logging.INFO if level > logging.DEBUG else logging.DEBUG)
    logging.getLogger("httpcore").setLevel(logging.WARNING if level > logging.DEBUG else logging.DEBUG)

    _CONFIGURED = True
