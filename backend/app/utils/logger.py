"""日志工具。"""

from __future__ import annotations

import logging
import os
import sys

_configured = False


def get_logger(name: str, level: str | None = None) -> logging.Logger:
    global _configured
    if not _configured:
        log_level = level or os.environ.get("TENNISCLIP_LOG_LEVEL", "INFO")
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
        root = logging.getLogger("tennisclip")
        root.addHandler(handler)
        root.setLevel(log_level.upper())
        _configured = True
    return logging.getLogger(f"tennisclip.{name}")
