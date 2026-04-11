"""Load and save integration credentials stored in the local .env file."""

import os
from pathlib import Path

from dotenv import dotenv_values

ENV_FILE = Path(__file__).resolve().parent / ".env"

DEFAULT_INTEGRATIONS = {
    "TOGGL_API_TOKEN": "",
    "TOGGL_WORKSPACE_ID": "",
    "STRIPE_API_KEY": "",
}


def load_integration_settings():
    """Return persisted integration credentials from the app-local .env file."""
    values = {**DEFAULT_INTEGRATIONS}
    if ENV_FILE.exists():
        parsed = dotenv_values(ENV_FILE)
        for key in values:
            values[key] = str(parsed.get(key) or "")
    else:
        for key in values:
            values[key] = os.getenv(key, "")
    return values


def save_integration_settings(settings):
    """Persist integration credentials back to the app-local .env file."""
    existing = {}
    if ENV_FILE.exists():
        existing.update({k: v or "" for k, v in dotenv_values(ENV_FILE).items()})

    for key in DEFAULT_INTEGRATIONS:
        existing[key] = str(settings.get(key, "") or "")
        if existing[key]:
            os.environ[key] = existing[key]
        elif key in os.environ:
            del os.environ[key]

    lines = [f"{key}={value}" for key, value in existing.items()]
    ENV_FILE.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
