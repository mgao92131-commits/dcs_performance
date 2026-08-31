"""Runtime defaults for connecting to the local DCS service."""

from __future__ import annotations

import os


DEFAULT_DCS_SERVICE_BASE_URL = os.environ.get(
    "DCS_SERVICE_BASE_URL",
    "http://192.168.1.10:8088",
)
