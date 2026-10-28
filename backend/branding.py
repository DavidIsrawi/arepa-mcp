"""Shared event copy; workshop access details are supplied by the organizer."""

import os
from urllib.parse import urlsplit


STORE_NAME = "Arepa Lab"
EVENT_NAME = "2026 SHPE National Convention"
EVENT_LOCATION = "Indianapolis, Indiana"
EVENT_DATES = "October 28–31, 2026"


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
        "training_key": os.environ.get("WORKSHOP_TRAINING_KEY", "").strip(),
    }
