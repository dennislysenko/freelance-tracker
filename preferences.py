"""Centralized preferences management using macOS standard locations."""

import json
from pathlib import Path

# Use macOS standard locations
APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "TogglMenuBar"
CACHE_DIR = Path.home() / "Library" / "Caches" / "TogglMenuBar"
PREFERENCES_FILE = APP_SUPPORT_DIR / "preferences.json"

# Create directories
APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Default preferences
DEFAULT_PREFERENCES = {
    "refresh_interval": 1800,  # 30 minutes in seconds
    "show_notifications": True,
    "daily_goal": 400,
    "weekly_goal": 2000,
    "monthly_goal": 8000,
    "show_hours": True,
    "menu_bar_format": "💰 ${total:.0f}",  # Can be customized
    "auto_start": True,
    "cache_ttl_projects": 86400,  # 24 hours
    "cache_ttl_today": 1800,  # 30 minutes
    "project_targets": {},  # Optional monthly hour targets by project name: {"ProjectName": 40}
    "vacation_days_per_month": 4,  # Vacation/PTO days to exclude from projection
}


def load_preferences():
    """Load preferences from disk, or create defaults."""
    if PREFERENCES_FILE.exists():
        try:
            with open(PREFERENCES_FILE, 'r') as f:
                prefs = json.load(f)
                # Merge with defaults (in case new keys were added)
                return {**DEFAULT_PREFERENCES, **prefs}
        except Exception as e:
            print(f"Error loading preferences: {e}")
            return DEFAULT_PREFERENCES.copy()
    else:
        # Create default preferences file
        save_preferences(DEFAULT_PREFERENCES)
        return DEFAULT_PREFERENCES.copy()


def save_preferences(preferences):
    """Save preferences to disk."""
    with open(PREFERENCES_FILE, 'w') as f:
        json.dump(preferences, f, indent=2)


def get_preference(key, default=None):
    """Get a single preference value."""
    prefs = load_preferences()
    return prefs.get(key, default)


def set_preference(key, value):
    """Set a single preference value."""
    prefs = load_preferences()
    prefs[key] = value
    save_preferences(prefs)


def reset_preferences():
    """Reset all preferences to defaults."""
    save_preferences(DEFAULT_PREFERENCES)


# Export paths for use by other modules
__all__ = [
    'APP_SUPPORT_DIR',
    'CACHE_DIR',
    'PREFERENCES_FILE',
    'load_preferences',
    'save_preferences',
    'get_preference',
    'set_preference',
    'reset_preferences',
]
