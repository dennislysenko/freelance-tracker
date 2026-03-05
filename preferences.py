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

# Default preferences (only fields actually used in the app)
DEFAULT_PREFERENCES = {
    "cache_ttl_projects": 86400,  # 24 hours
    "cache_ttl_today": 1800,  # 30 minutes
    "vacation_days_per_month": 4,  # Vacation/PTO days to exclude from projection
    "project_targets": {},  # Optional monthly hour targets by project name: {"ProjectName": 40}
    "retainer_hourly_rates": {},  # Optional hourly overrides by project name: {"ProjectName": 150}
    "projects": {},  # Project definitions by name: see docs/SOT.md for schema
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


def validate_preferences(prefs):
    """
    Validate preferences structure and types.

    Args:
        prefs: Dictionary containing preferences to validate

    Returns:
        List of error messages (empty list if valid)
    """
    errors = []

    # Required fields with (type, validator_function, error_message)
    # validator_function is None if only type checking is needed
    required_fields = {
        'cache_ttl_projects': (int, lambda x: x > 0, "must be a positive integer"),
        'cache_ttl_today': (int, lambda x: x > 0, "must be a positive integer"),
        'vacation_days_per_month': (int, lambda x: 0 <= x <= 31, "must be between 0 and 31"),
    }

    # Check required fields
    for field, (expected_type, validator, error_msg) in required_fields.items():
        # Check if field exists
        if field not in prefs:
            errors.append(f"Missing required field: '{field}'")
            continue

        value = prefs[field]

        # Type checking
        if not isinstance(value, expected_type):
            errors.append(
                f"'{field}': expected {expected_type.__name__}, got {type(value).__name__}"
            )
            continue

        # Custom validation
        if validator is not None:
            try:
                if not validator(value):
                    errors.append(f"'{field}': {error_msg} (got {value})")
            except Exception as e:
                errors.append(f"'{field}': validation error - {str(e)}")

    # Optional field: project_targets
    if 'project_targets' in prefs:
        project_targets = prefs['project_targets']

        # Type check - must be a dict
        if not isinstance(project_targets, dict):
            errors.append("'project_targets': must be an object/dictionary")
        else:
            # Validate each project target entry
            for project_name, hours in project_targets.items():
                # Project name must be string
                if not isinstance(project_name, str):
                    errors.append(
                        f"'project_targets': key '{project_name}' must be a string"
                    )
                    continue

                # Hours must be a number (int or float)
                if not isinstance(hours, (int, float)):
                    errors.append(
                        f"'project_targets.{project_name}': must be a number, got {type(hours).__name__}"
                    )
                    continue

                # Hours must be non-negative
                if hours < 0:
                    errors.append(
                        f"'project_targets.{project_name}': must be non-negative (got {hours})"
                    )

    # Optional field: retainer_hourly_rates
    if 'retainer_hourly_rates' in prefs:
        retainer_hourly_rates = prefs['retainer_hourly_rates']

        # Type check - must be a dict
        if not isinstance(retainer_hourly_rates, dict):
            errors.append("'retainer_hourly_rates': must be an object/dictionary")
        else:
            # Validate each retainer rate entry
            for project_name, rate in retainer_hourly_rates.items():
                # Project name must be string
                if not isinstance(project_name, str):
                    errors.append(
                        f"'retainer_hourly_rates': key '{project_name}' must be a string"
                    )
                    continue

                # Rate must be a number (int or float)
                if not isinstance(rate, (int, float)):
                    errors.append(
                        f"'retainer_hourly_rates.{project_name}': must be a number, got {type(rate).__name__}"
                    )
                    continue

                # Rate must be positive
                if rate <= 0:
                    errors.append(
                        f"'retainer_hourly_rates.{project_name}': must be greater than 0 (got {rate})"
                    )

    # Optional field: projects
    if 'projects' in prefs:
        projects = prefs['projects']

        if not isinstance(projects, dict):
            errors.append("'projects': must be an object/dictionary")
        else:
            valid_billing_types = {'hourly', 'hourly_with_cap', 'fixed_monthly'}
            valid_hour_tracking = {'required', 'soft', 'none'}

            for project_name, defn in projects.items():
                prefix = f"'projects.{project_name}'"

                if not isinstance(defn, dict):
                    errors.append(f"{prefix}: must be an object")
                    continue

                billing_type = defn.get('billing_type')
                if billing_type not in valid_billing_types:
                    errors.append(
                        f"{prefix}: 'billing_type' must be one of {sorted(valid_billing_types)}, got {billing_type!r}"
                    )
                    continue

                if billing_type == 'hourly_with_cap':
                    hourly_rate = defn.get('hourly_rate')
                    cap_hours = defn.get('cap_hours')
                    if not isinstance(hourly_rate, (int, float)) or hourly_rate <= 0:
                        errors.append(f"{prefix}: 'hourly_rate' must be a positive number")
                    if not isinstance(cap_hours, (int, float)) or cap_hours <= 0:
                        errors.append(f"{prefix}: 'cap_hours' must be a positive number")

                elif billing_type == 'fixed_monthly':
                    monthly_amount = defn.get('monthly_amount')
                    if not isinstance(monthly_amount, (int, float)) or monthly_amount <= 0:
                        errors.append(f"{prefix}: 'monthly_amount' must be a positive number")

                    hour_tracking = defn.get('hour_tracking', 'none')
                    if hour_tracking not in valid_hour_tracking:
                        errors.append(
                            f"{prefix}: 'hour_tracking' must be one of {sorted(valid_hour_tracking)}, got {hour_tracking!r}"
                        )

                    if hour_tracking in ('required', 'soft'):
                        target_hours = defn.get('target_hours')
                        if not isinstance(target_hours, (int, float)) or target_hours <= 0:
                            errors.append(
                                f"{prefix}: 'target_hours' must be a positive number when hour_tracking is '{hour_tracking}'"
                            )

    return errors


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
    'validate_preferences',
]
