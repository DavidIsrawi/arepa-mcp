"""Shared branding, static asset versions, and organizer-supplied workshop access."""

from functools import lru_cache
import hashlib
import os
from pathlib import Path
from urllib.parse import urlsplit


STORE_NAME = "Arepa Lab"
EVENT_NAME = "2026 SHPE National Convention"
EVENT_LOCATION = "Indianapolis, Indiana"
EVENT_DATES = "October 28–31, 2026"


@lru_cache
def asset_version(filename: str) -> str:
    content = (Path(__file__).parent / "static" / filename).read_bytes()
    return hashlib.sha256(content).hexdigest()[:12]


def workshop_context() -> dict[str, str]:
    url = os.environ.get("WORKSHOP_URL", "https://aka.ms/MicrosoftArepas").strip()
    if url:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("WORKSHOP_URL must be an absolute HTTP(S) URL without credentials.")
    return {
        "store_name": STORE_NAME,
        "event_name": EVENT_NAME,
        "event_location": EVENT_LOCATION,
        "event_dates": EVENT_DATES,
        "workshop_url": url,
        "training_key": os.environ.get("WORKSHOP_TRAINING_KEY", "MScupcake").strip(),
    }
