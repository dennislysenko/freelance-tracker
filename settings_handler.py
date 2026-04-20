"""Python-side handlers for settings:* bridge messages.

Mirrors the save/validation flow of `preferences_window.handleSave_`, but
driven by a JSON payload from the WebKit settings view instead of AppKit
widgets. Persistence and validation rules are unchanged from the spec.

Phase 1 handles an empty payload (Save round-trip with no fields yet) so
the scaffold is testable end-to-end. Later phases will read concrete keys
out of `payload` and apply them to `preferences.json` / `.env` /
`retainer_carryover.json`.
"""

from __future__ import annotations

from typing import Any, Dict

from carryover import set_balance, get_previous_month_str
from preferences import load_preferences, save_preferences, validate_preferences
from integrations import load_integration_settings, save_integration_settings
from settings_view import defn_key_to_fields


# Keys that the settings view is authoritative for and sends as-is.
# Structured payloads (projects_rows, billing_reminders_rows, mapping_rows)
# are translated into their canonical keys below before persistence.
_SETTINGS_KEYS = (
    "cache_ttl_projects",
    "cache_ttl_today",
    "vacation_days_per_month",
    "project_targets",
    "stripe_project_customers",
    "upwork_contracts",
    "billing_reminders",
)


def _safe_float(value):
    """Parse a numeric string safely; silent fallback to 0.0 matches native quirk."""
    try:
        return float(str(value).strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def translate_projects_rows(rows):
    """Return (projects_config, carryover_writes) from a projects_rows payload.

    carryover_writes is a list of (project_name, hours) tuples to apply for
    the previous month. Empty list means no carryover changes.

    Mirrors `preferences_window.handleSave_` for the Projects tab:
      - Rows without a project selected are dropped.
      - `hourly`: stores only billing_type.
      - `hourly_with_cap`: stores rate/cap and optional last_billed_date.
        If last_billed_date is set, force carryover to 0.0 for previous month.
        If blank, only write carryover when the user entered a value.
      - `fixed_monthly`: stores monthly_amount + hour_tracking, and
        target_hours when tracking is required/soft.
        For required, write carryover only if the user entered a value.
    """
    projects_config = {}
    carryover_writes = []
    for row in rows or []:
        name = str(row.get("name", "") or "").strip()
        if not name:
            continue
        data_type = row.get("type") or "hourly"
        billing_type, hour_tracking = defn_key_to_fields(data_type)
        defn = {"billing_type": billing_type}

        if billing_type == "hourly_with_cap":
            defn["hourly_rate"] = _safe_float(row.get("hourly_rate"))
            defn["cap_hours"] = _safe_float(row.get("cap_hours"))
            last_billed = str(row.get("last_billed_date", "") or "").strip()
            if last_billed:
                defn["last_billed_date"] = last_billed
        elif billing_type == "fixed_monthly":
            defn["monthly_amount"] = _safe_float(row.get("monthly_amount"))
            defn["hour_tracking"] = hour_tracking
            if hour_tracking in ("required", "soft"):
                defn["target_hours"] = _safe_float(row.get("target_hours"))

        projects_config[name] = defn

        needs_carryover = (
            billing_type == "hourly_with_cap"
            or (billing_type == "fixed_monthly" and hour_tracking == "required")
        )
        if needs_carryover:
            has_last_billed = billing_type == "hourly_with_cap" and defn.get("last_billed_date")
            if has_last_billed:
                # last_billed_date supersedes manual carryover.
                carryover_writes.append((name, 0.0))
            else:
                carry_str = str(row.get("carryover", "") or "").strip()
                if carry_str:
                    try:
                        carryover_writes.append((name, float(carry_str)))
                    except ValueError:
                        # Silently skip unparseable carryover, same as native.
                        pass

    return projects_config, carryover_writes


def translate_mapping_rows(rows):
    """Return (stripe_project_customers, upwork_contracts) maps.

    Per spec:
      - `stripe_project_customers` gets rows with non-placeholder project AND
        non-blank customer id.
      - `upwork_contracts` gets rows with non-placeholder project AND non-blank
        contract id.
    """
    stripe_map = {}
    upwork_map = {}
    for row in rows or []:
        project_name = str(row.get("project_name", "") or "").strip()
        if not project_name:
            continue
        customer_id = str(row.get("customer_id", "") or "").strip()
        upwork_id = str(row.get("upwork_contract_id", "") or "").strip()
        if customer_id:
            stripe_map[project_name] = customer_id
        if upwork_id:
            upwork_map[project_name] = upwork_id
    return stripe_map, upwork_map


def translate_reminder_rows(rows):
    """Return a `billing_reminders` array from a reminder_rows payload.

    Input row shape: {enabled, project_name, task, day_key, time}
    where day_key is `weekday:<name>` or `dom:<int>`.

    Drop rule mirrors the UI/native: a row is skipped only when all of
    project blank, time blank, enabled off — any partial row falls through
    to `validate_preferences` which reports the specific issue.
    """
    out = []
    for row in rows or []:
        enabled = bool(row.get("enabled"))
        project_name = str(row.get("project_name", "") or "").strip()
        time_str = str(row.get("time", "") or "").strip()
        if not project_name and not time_str and not enabled:
            continue

        entry = {
            "enabled": enabled,
            "project_name": project_name,
            "task": str(row.get("task", "invoice") or "invoice"),
            "time": time_str,
        }

        day_key = str(row.get("day_key", "") or "")
        if day_key.startswith("dom:"):
            try:
                entry["day_of_month"] = int(day_key.split(":", 1)[1])
            except ValueError:
                pass
        else:
            weekday = day_key.split(":", 1)[1] if ":" in day_key else "friday"
            entry["weekday"] = weekday

        out.append(entry)
    return out


def apply_settings_save(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Apply a settings save payload. Returns `{ok, errors}`.

    Phase 1 contract:
      - Empty payload is a valid no-op round-trip (for scaffold testing).
      - If any of the authoritative keys are present, they overwrite the
        corresponding preference. Unknown keys survive as-is.
      - Integration credentials (`integrations` sub-object) are persisted
        via `.env` with the same quirks as `preferences_window.handleSave_`.
    """
    errors = []
    payload = payload or {}

    # Merge preferences
    current = load_preferences()
    new_prefs = current.copy()
    prefs_changed = False
    for key in _SETTINGS_KEYS:
        if key in payload:
            new_prefs[key] = payload[key]
            prefs_changed = True

    # Translate structured project rows into the canonical `projects` map
    # plus carryover writes. Applied after the verbatim-key loop so an
    # explicit `projects` key is still honored for tooling/raw payloads.
    carryover_writes = []
    if "projects_rows" in payload:
        projects_config, carryover_writes = translate_projects_rows(payload["projects_rows"])
        new_prefs["projects"] = projects_config
        prefs_changed = True

    if "billing_reminders_rows" in payload:
        new_prefs["billing_reminders"] = translate_reminder_rows(payload["billing_reminders_rows"])
        prefs_changed = True

    if "mapping_rows" in payload:
        stripe_map, upwork_map = translate_mapping_rows(payload["mapping_rows"])
        new_prefs["stripe_project_customers"] = stripe_map
        new_prefs["upwork_contracts"] = upwork_map
        prefs_changed = True

    # Integration credentials
    integrations_payload = payload.get("integrations")
    integrations_changed = False
    integration_settings = None
    if isinstance(integrations_payload, dict):
        integration_settings = {
            "TOGGL_API_TOKEN": str(integrations_payload.get("TOGGL_API_TOKEN", "") or "").strip(),
            "TOGGL_WORKSPACE_ID": str(integrations_payload.get("TOGGL_WORKSPACE_ID", "") or "").strip(),
            "STRIPE_API_KEY": str(integrations_payload.get("STRIPE_API_KEY", "") or "").strip(),
        }
        if not integration_settings["TOGGL_API_TOKEN"]:
            errors.append("Toggl API Token is required")
        stripe_key = integration_settings["STRIPE_API_KEY"]
        if stripe_key and not stripe_key.startswith("sk_"):
            errors.append("Stripe API Key must start with 'sk_'")
        integrations_changed = True

    # Validate
    if prefs_changed:
        errors.extend(validate_preferences(new_prefs))

    if errors:
        return {"ok": False, "errors": errors}

    # Persist
    if prefs_changed:
        save_preferences(new_prefs)
    if integrations_changed and integration_settings is not None:
        save_integration_settings(integration_settings)
    if carryover_writes:
        prev_ym, _ = get_previous_month_str()
        for name, hours in carryover_writes:
            set_balance(name, prev_ym, hours)

    return {"ok": True, "errors": []}


def load_settings_snapshot() -> Dict[str, Any]:
    """Return the current preferences + integrations for the settings view.

    The load path always goes through the live `preferences.json` / `.env` so
    the UI reflects on-disk state, not any stale in-memory cache.
    """
    prefs = load_preferences()
    integrations = load_integration_settings()
    return {
        "preferences": prefs,
        "integrations": integrations,
    }
