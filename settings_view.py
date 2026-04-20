"""Settings view rendered inside the WebKit dashboard popover.

This module owns the HTML/CSS/JS for the in-popover preferences UI and the
Python-side handlers that service `settings:*` bridge messages. The native
AppKit preferences window in `preferences_window.py` still exists as the
rumps fallback path; behavior/persistence here must match it 1:1.

Bridge message protocol (string payloads, ':'-separated for simple forms;
JSON after the first space for structured payloads):

    router:settings
    router:dashboard
    settings:save <json>
    settings:reset
    settings:refresh_stripe <json>
    settings:test_notification
    settings:open_audit_log

Replies are delivered to JS via `window.__settingsAck({...})`.
"""

from __future__ import annotations

import json

from carryover import get_balance, get_previous_month_str
from preferences import CACHE_DIR


# Tab order matches docs/settings-implementation-spec.md §Tab Order
TAB_ORDER = [
    ("caching", "Caching"),
    ("work", "Work Planning"),
    ("projects", "Projects"),
    ("billing", "Billing"),
    ("integrations", "Integrations"),
    ("advanced", "Advanced"),
]


# Maps UI label -> (billing_type, hour_tracking)
BILLING_TYPE_OPTIONS = [
    ("Hourly",                    "hourly",          None),
    ("Hourly with cap",           "hourly_with_cap", None),
    ("Fixed + required hours",    "fixed_monthly",   "required"),
    ("Fixed + soft target",       "fixed_monthly",   "soft"),
    ("Fixed flat (no tracking)",  "fixed_monthly",   "none"),
]

# (label, kind, value) — kind is "weekday" or "day_of_month"
BILLING_REMINDER_DAY_OPTIONS = (
    [
        ("Monday",    "weekday", "monday"),
        ("Tuesday",   "weekday", "tuesday"),
        ("Wednesday", "weekday", "wednesday"),
        ("Thursday",  "weekday", "thursday"),
        ("Friday",    "weekday", "friday"),
        ("Saturday",  "weekday", "saturday"),
        ("Sunday",    "weekday", "sunday"),
    ]
    + [(f"Day {d} of month", "day_of_month", d) for d in range(1, 29)]
    + [
        ("Last day of month", "day_of_month", -1),
        ("2nd-to-last day",   "day_of_month", -2),
        ("3rd-to-last day",   "day_of_month", -3),
    ]
)


def _esc(text):
    """Escape HTML entities. Mirrors the helper in dashboard_panel.py."""
    return (
        str(text)
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
    )


def get_toggl_project_names():
    """Project names from the Toggl projects cache, alphabetical. []= on miss."""
    cache_file = CACHE_DIR / "projects.json"
    if cache_file.exists():
        try:
            with open(cache_file, 'r') as f:
                projects = json.load(f)
            return sorted(p['name'] for p in projects.values() if p.get('name'))
        except Exception:
            pass
    return []


# ---------- CSS ----------
# Returned with single braces; callers embed this via an f-string substitution
# like `f"<style>...{settings_css}...</style>"`, so the interpolator inserts the
# text verbatim and CSS braces remain literal.

def generate_settings_css():
    return """
    .settings-root {
        min-height: 100%;
        padding: 0;
        width: 100%;
        max-width: 100vw;
        box-sizing: border-box;
        overflow-x: hidden;
    }
    .settings-root * { box-sizing: border-box; }

    body[data-view="dashboard"] .settings-root { display: none; }
    body[data-view="settings"] .dashboard-root { display: none; }
    body[data-view="settings"] .footer { display: none; }
    /* Allow the settings view to scroll when its content exceeds popover height. */
    body[data-view="settings"] {
        overflow-y: auto;
        scrollbar-width: thin;
        scrollbar-color: rgba(255,255,255,0.24) transparent;
    }
    body[data-view="settings"]::-webkit-scrollbar { width: 8px; }
    body[data-view="settings"]::-webkit-scrollbar-thumb {
        background: rgba(255,255,255,0.22);
        border-radius: 4px;
    }
    body[data-view="settings"]::-webkit-scrollbar-track { background: transparent; }

    .settings-header {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 10px 12px;
        border-bottom: 1px solid rgba(255,255,255,0.08);
        position: sticky;
        top: 0;
        background: #1c1c1e;
        z-index: 5;
    }

    .settings-back {
        flex: 0 0 auto;
        width: 28px;
        height: 28px;
        border: 0;
        border-radius: 8px;
        background: rgba(255,255,255,0.04);
        color: #c9d1d9;
        font-size: 14px;
        cursor: pointer;
        -webkit-appearance: none;
    }
    .settings-back:hover { background: rgba(255,255,255,0.1); }

    .settings-title {
        flex: 1;
        font-size: 13px;
        font-weight: 700;
        color: #c9d1d9;
        letter-spacing: 0.4px;
    }

    .settings-actions {
        display: flex;
        gap: 6px;
        flex: 0 0 auto;
    }

    .settings-btn {
        border: 1px solid rgba(255,255,255,0.12);
        border-radius: 8px;
        background: rgba(255,255,255,0.04);
        color: #c9d1d9;
        font-size: 11px;
        font-family: inherit;
        padding: 6px 10px;
        cursor: pointer;
        -webkit-appearance: none;
        white-space: nowrap;
    }
    .settings-btn:hover { background: rgba(255,255,255,0.1); }
    .settings-btn.primary {
        background: rgba(88,166,255,0.14);
        color: #58a6ff;
        border-color: rgba(88,166,255,0.28);
    }
    .settings-btn.primary:hover { background: rgba(88,166,255,0.22); }
    .settings-btn:disabled { opacity: 0.55; cursor: default; }

    .settings-tabs {
        display: flex;
        gap: 0;
        padding: 4px 6px;
        border-bottom: 1px solid rgba(255,255,255,0.05);
        overflow-x: auto;
        scrollbar-width: none;
    }
    .settings-tabs::-webkit-scrollbar { display: none; }

    .settings-tab {
        padding: 5px 7px;
        font-size: 11px;
        color: #8b949e;
        background: transparent;
        border: 0;
        border-radius: 6px;
        cursor: pointer;
        -webkit-appearance: none;
        white-space: nowrap;
    }
    .settings-tab:hover { color: #c9d1d9; }
    .settings-tab.active {
        background: rgba(255,255,255,0.06);
        color: #c9d1d9;
    }

    .settings-body {
        padding: 12px;
        padding-bottom: 20px;
    }

    .settings-panel { display: none; }
    .settings-panel.active { display: block; }

    .settings-panel-title {
        font-size: 12px;
        font-weight: 700;
        color: #c9d1d9;
        margin-bottom: 8px;
        letter-spacing: 0.2px;
    }

    .settings-help {
        font-size: 11px;
        color: #8b949e;
        line-height: 1.5;
        margin-bottom: 12px;
    }

    .settings-field {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-bottom: 10px;
    }

    .settings-field label {
        flex: 0 0 160px;
        font-size: 12px;
        color: #b0b8c1;
    }

    .settings-field input[type="text"],
    .settings-field input[type="password"],
    .settings-field input[type="number"],
    .settings-field input[type="date"],
    .settings-field select {
        flex: 1;
        padding: 6px 8px;
        border: 1px solid rgba(255,255,255,0.12);
        border-radius: 6px;
        background: rgba(255,255,255,0.04);
        color: #c9d1d9;
        font: inherit;
        font-size: 12px;
        color-scheme: dark;
        -webkit-appearance: none;
    }

    .settings-field input:focus,
    .settings-field select:focus {
        outline: none;
        border-color: rgba(88,166,255,0.55);
        background: rgba(255,255,255,0.06);
    }

    .settings-field-error {
        font-size: 11px;
        color: #f85149;
        margin-top: 4px;
        margin-left: 170px;
    }

    .settings-row {
        display: grid;
        gap: 8px;
        padding: 8px;
        border-radius: 8px;
        background: rgba(255,255,255,0.03);
        margin-bottom: 8px;
    }

    .settings-row-header {
        display: flex;
        align-items: center;
        gap: 8px;
    }

    .settings-row-remove {
        flex: 0 0 auto;
        width: 22px;
        height: 22px;
        border: 0;
        border-radius: 6px;
        background: rgba(255,255,255,0.05);
        color: #8b949e;
        cursor: pointer;
        font-size: 13px;
        -webkit-appearance: none;
    }
    .settings-row-remove:hover {
        background: rgba(248,81,73,0.18);
        color: #f85149;
    }

    .settings-add {
        margin-top: 4px;
        padding: 7px 10px;
        border: 1px dashed rgba(255,255,255,0.18);
        border-radius: 8px;
        background: transparent;
        color: #8b949e;
        font-size: 11px;
        cursor: pointer;
        width: 100%;
        -webkit-appearance: none;
    }
    .settings-add:hover {
        color: #c9d1d9;
        border-color: rgba(255,255,255,0.32);
    }

    .settings-errors {
        display: none;
        padding: 10px 12px;
        margin: 10px 12px;
        border-radius: 8px;
        background: rgba(248,81,73,0.08);
        border: 1px solid rgba(248,81,73,0.24);
        color: #f0b4af;
        font-size: 11px;
        line-height: 1.5;
    }
    .settings-errors.show { display: block; }
    .settings-errors ul { margin: 4px 0 0 18px; padding: 0; }

    .settings-toast {
        position: fixed;
        right: 12px;
        top: 12px;
        padding: 6px 10px;
        border-radius: 8px;
        background: rgba(63,185,80,0.18);
        color: #3fb950;
        border: 1px solid rgba(63,185,80,0.36);
        font-size: 11px;
        opacity: 0;
        transition: opacity 0.2s ease;
        z-index: 30;
    }
    .settings-toast.show { opacity: 1; }

    /* Projects tab: rows own the conditional-field visibility via data-type */
    .pd-row .pd-field { display: none; }
    .pd-row .settings-row-header {
        display: flex;
        gap: 8px;
        align-items: center;
    }
    .pd-row .settings-row-header .pd-name { flex: 1 1 auto; }
    .pd-row .settings-row-header .pd-type { flex: 0 0 auto; }

    .pd-row[data-type="hourly_with_cap"] .pd-rate-field,
    .pd-row[data-type="hourly_with_cap"] .pd-cap-field,
    .pd-row[data-type="hourly_with_cap"] .pd-last-billed-field,
    .pd-row[data-type="fixed_required"] .pd-monthly-field,
    .pd-row[data-type="fixed_required"] .pd-target-field,
    .pd-row[data-type="fixed_soft"] .pd-monthly-field,
    .pd-row[data-type="fixed_soft"] .pd-target-field,
    .pd-row[data-type="fixed_flat"] .pd-monthly-field {
        display: flex;
    }
    /* Manual carryover: show for fixed_required always, and for hourly_with_cap
       only when there is no last_billed_date (date takes precedence). */
    .pd-row[data-type="fixed_required"] .pd-carryover-field,
    .pd-row[data-type="hourly_with_cap"][data-has-last-billed="0"] .pd-carryover-field {
        display: flex;
    }

    /* Billing reminders */
    .br-row .settings-row-header {
        display: flex;
        gap: 8px;
        align-items: center;
        flex-wrap: wrap;
    }
    .br-toggle {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        font-size: 11px;
        color: #b0b8c1;
    }
    .br-row .br-project { flex: 1 1 140px; min-width: 140px; }
    .br-row .br-day { flex: 0 1 160px; }
    .br-row .br-time { flex: 0 0 70px; text-align: center; }

    /* Credentials: masked field with show/hide eye toggle. */
    .creds-field { position: relative; }
    .creds-field input[type="password"],
    .creds-field input[type="text"] {
        padding-right: 32px;
    }
    .creds-eye {
        position: absolute;
        right: 6px;
        top: 50%;
        transform: translateY(-50%);
        width: 24px;
        height: 22px;
        border: 0;
        border-radius: 6px;
        background: transparent;
        color: #8b949e;
        cursor: pointer;
        font-size: 12px;
        -webkit-appearance: none;
    }
    .creds-eye:hover { color: #c9d1d9; background: rgba(255,255,255,0.06); }
    .creds-eye.on { color: #58a6ff; }

    /* Mapping rows: reflow to two visual lines so 420px popover fits.
       Line 1: project + remove (×). Line 2: customer + upwork contract id. */
    .map-row .settings-row-header {
        display: grid;
        grid-template-columns: 1fr auto;
        grid-template-areas:
            "project remove"
            "customer upwork";
        gap: 6px 8px;
        align-items: center;
    }
    .map-row .map-project { grid-area: project; min-width: 0; }
    .map-row .settings-row-remove { grid-area: remove; }
    .map-row .map-customer { grid-area: customer; min-width: 0; }
    .map-row .map-upwork { grid-area: upwork; min-width: 0; flex: unset; }
    """


# ---------- HTML ----------

def _render_tab_nav():
    buttons = []
    for idx, (key, label) in enumerate(TAB_ORDER):
        cls = "settings-tab" + (" active" if idx == 0 else "")
        buttons.append(
            f'<button class="{cls}" data-tab="{key}" '
            f'onclick="settingsSelectTab(\'{key}\')">{_esc(label)}</button>'
        )
    return '<div class="settings-tabs">' + "".join(buttons) + '</div>'


def _render_panel_caching(prefs):
    p_ttl = int(prefs.get("cache_ttl_projects", 86400) or 0)
    t_ttl = int(prefs.get("cache_ttl_today", 1800) or 0)
    return f"""
    <div class="settings-panel-title">Caching</div>
    <div class="settings-help">How long cached data is reused before hitting the Toggl API again.</div>
    <div class="settings-field">
        <label for="set_cache_ttl_projects">Projects Cache TTL (sec)</label>
        <input type="number" id="set_cache_ttl_projects" min="1" value="{p_ttl}">
    </div>
    <div class="settings-field">
        <label for="set_cache_ttl_today">Today Cache TTL (sec)</label>
        <input type="number" id="set_cache_ttl_today" min="1" value="{t_ttl}">
    </div>
    """


def _render_panel_work(prefs):
    vac = int(prefs.get("vacation_days_per_month", 4) or 0)
    targets = prefs.get("project_targets", {}) or {}
    rows_html = []
    for name, hours in targets.items():
        try:
            hours_int = int(hours)
        except (TypeError, ValueError):
            hours_int = 0
        rows_html.append(
            '<div class="settings-row" data-row-type="project-target">'
            '<div class="settings-row-header">'
            f'<input type="text" class="pt-name" placeholder="Project name" value="{_esc(name)}">'
            f'<input type="number" class="pt-hours" min="0" value="{hours_int}" style="flex:0 0 90px;">'
            '<button class="settings-row-remove" aria-label="Remove"'
            ' onclick="removeSettingsRow(this)">\u00d7</button>'
            '</div></div>'
        )
    return f"""
    <div class="settings-panel-title">Work Planning</div>
    <div class="settings-field">
        <label for="set_vacation_days">Vacation Days/Month</label>
        <input type="number" id="set_vacation_days" min="0" max="31" value="{vac}">
    </div>
    <div class="settings-help">
        Monthly hour goals per project. Blank-name rows are dropped on save.
    </div>
    <button class="settings-add" type="button" onclick="addProjectTargetRow()">+ Add target</button>
    <div id="project_targets_rows">
        {"".join(rows_html)}
    </div>
    """


# Map UI data-type to (billing_type, hour_tracking). The UI uses compound
# keys ("fixed_required", "fixed_soft", "fixed_flat") so CSS can drive
# conditional-field visibility via one `data-type` attribute per row.
DEFN_TYPE_KEYS = [
    ("hourly",          "hourly",         None,       "Hourly"),
    ("hourly_with_cap", "hourly_with_cap", None,      "Hourly with cap"),
    ("fixed_required",  "fixed_monthly",  "required", "Fixed + required hours"),
    ("fixed_soft",      "fixed_monthly",  "soft",     "Fixed + soft target"),
    ("fixed_flat",      "fixed_monthly",  "none",     "Fixed flat (no tracking)"),
]


def defn_key_to_fields(data_type):
    """Data-type string -> (billing_type, hour_tracking)."""
    for key, bt, ht, _ in DEFN_TYPE_KEYS:
        if key == data_type:
            return bt, ht
    return "hourly", None


def _defn_to_data_type(defn):
    billing_type = (defn or {}).get("billing_type", "hourly")
    hour_tracking = (defn or {}).get("hour_tracking")
    for key, bt, ht, _ in DEFN_TYPE_KEYS:
        if bt == billing_type and ht == hour_tracking:
            return key
    return "hourly"


def _fmt_number(value):
    """Mirror preferences_window._fmt: strip trailing '.0' when integral; '' for 0/blank."""
    try:
        f = float(value or 0)
    except (TypeError, ValueError):
        return ""
    if f == 0:
        return ""
    return str(int(f)) if f == int(f) else str(f)


def _render_project_defn_row(name, defn, toggl_names, prev_ym):
    defn = defn or {}
    dtype = _defn_to_data_type(defn)
    last_billed = str(defn.get("last_billed_date") or "").strip()
    has_lbd = "1" if last_billed else "0"

    # Name dropdown: cached Toggl names, plus the saved name if it's dropped from cache.
    names = list(toggl_names)
    if name and name not in names:
        names.insert(0, name)
    name_opts = ['<option value="">\u2014</option>']
    for n in names:
        sel = " selected" if n == name else ""
        name_opts.append(f'<option value="{_esc(n)}"{sel}>{_esc(n)}</option>')
    name_select = "".join(name_opts)

    type_opts = []
    for key, _bt, _ht, label in DEFN_TYPE_KEYS:
        sel = " selected" if key == dtype else ""
        type_opts.append(f'<option value="{key}"{sel}>{_esc(label)}</option>')
    type_select = "".join(type_opts)

    monthly = _fmt_number(defn.get("monthly_amount"))
    target = _fmt_number(defn.get("target_hours"))
    rate = _fmt_number(defn.get("hourly_rate"))
    cap = _fmt_number(defn.get("cap_hours"))
    carry_initial = _fmt_number(get_balance(name, prev_ym)) if name else ""

    _, prev_label = get_previous_month_str()

    return f"""
    <div class="settings-row pd-row" data-row-type="project-defn"
         data-type="{dtype}" data-has-last-billed="{has_lbd}">
        <div class="settings-row-header">
            <select class="pd-name">{name_select}</select>
            <select class="pd-type" onchange="onProjectTypeChange(this)">{type_select}</select>
            <button class="settings-row-remove" type="button"
                    aria-label="Remove" onclick="removeSettingsRow(this)">\u00d7</button>
        </div>
        <div class="settings-field pd-field pd-monthly-field">
            <label>Monthly $</label>
            <input type="text" inputmode="decimal" class="pd-monthly"
                   value="{_esc(monthly)}" placeholder="0">
        </div>
        <div class="settings-field pd-field pd-target-field">
            <label>Target h</label>
            <input type="text" inputmode="decimal" class="pd-target"
                   value="{_esc(target)}" placeholder="0">
        </div>
        <div class="settings-field pd-field pd-rate-field">
            <label>Rate $/h</label>
            <input type="text" inputmode="decimal" class="pd-rate"
                   value="{_esc(rate)}" placeholder="0">
        </div>
        <div class="settings-field pd-field pd-cap-field">
            <label>Cap h</label>
            <input type="text" inputmode="decimal" class="pd-cap"
                   value="{_esc(cap)}" placeholder="0">
        </div>
        <div class="settings-field pd-field pd-last-billed-field">
            <label>Last billed</label>
            <input type="date" class="pd-last-billed"
                   value="{_esc(last_billed)}" onchange="onLastBilledChange(this)">
        </div>
        <div class="settings-field pd-field pd-carryover-field">
            <label>{_esc(prev_label)} carryover h</label>
            <input type="text" inputmode="decimal" class="pd-carryover"
                   value="{_esc(carry_initial)}" placeholder="0">
        </div>
    </div>
    """


def _render_panel_projects(prefs):
    projects_config = prefs.get("projects") or {}
    toggl_names = get_toggl_project_names()
    prev_ym, _ = get_previous_month_str()
    rows = [
        _render_project_defn_row(name, defn, toggl_names, prev_ym)
        for name, defn in projects_config.items()
    ]
    names_json = json.dumps(toggl_names)
    return f"""
    <div class="settings-panel-title">Projects</div>
    <div class="settings-help">
        Project billing definitions. Fixed + required: monthly carryover applies.
        Fixed + soft: display only. Fixed flat: freeform amount. Rows without a
        project selected are dropped on save.
    </div>
    <button class="settings-add" type="button"
            onclick="addProjectDefnRow()">+ Add project</button>
    <div id="project_defn_rows" data-toggl-names='{_esc(names_json)}'>
        {"".join(rows)}
    </div>
    """


def _reminder_to_day_key(reminder):
    """Serialize a billing reminder to the compound option value used by the
    Day/Date <select>: `weekday:<name>` or `dom:<int>`.
    """
    dom = reminder.get("day_of_month") if isinstance(reminder, dict) else None
    if dom:
        return f"dom:{int(dom)}"
    weekday = (reminder or {}).get("weekday", "friday")
    return f"weekday:{weekday}"


def _day_options_html(selected_key="weekday:friday"):
    opts = []
    for label, kind, value in BILLING_REMINDER_DAY_OPTIONS:
        key = f"{'dom' if kind == 'day_of_month' else 'weekday'}:{value}"
        sel = " selected" if key == selected_key else ""
        opts.append(f'<option value="{_esc(key)}"{sel}>{_esc(label)}</option>')
    return "".join(opts)


def _render_reminder_row(reminder, toggl_names):
    reminder = reminder or {}
    # Native quirk: missing `enabled` validates as True but loads as unchecked.
    enabled = bool(reminder.get("enabled", False))
    project_name = str(reminder.get("project_name", "") or "").strip()
    time_str = str(reminder.get("time", "") or "").strip()
    day_key = _reminder_to_day_key(reminder)

    names = list(toggl_names)
    if project_name and project_name not in names:
        names.insert(0, project_name)
    name_opts = ['<option value="">\u2014</option>']
    for n in names:
        sel = " selected" if n == project_name else ""
        name_opts.append(f'<option value="{_esc(n)}"{sel}>{_esc(n)}</option>')
    return f"""
    <div class="settings-row br-row" data-row-type="billing-reminder">
        <div class="settings-row-header">
            <label class="br-toggle">
                <input type="checkbox" class="br-enabled"{' checked' if enabled else ''}>
                <span>Enabled</span>
            </label>
            <select class="br-project">{"".join(name_opts)}</select>
            <select class="br-day">{_day_options_html(day_key)}</select>
            <input type="text" class="br-time" placeholder="14:00"
                   value="{_esc(time_str)}" maxlength="5">
            <button class="settings-row-remove" type="button"
                    aria-label="Remove" onclick="removeSettingsRow(this)">\u00d7</button>
        </div>
    </div>
    """


def _render_panel_billing(prefs):
    reminders = prefs.get("billing_reminders") or []
    toggl_names = get_toggl_project_names()
    rows = [_render_reminder_row(r, toggl_names) for r in reminders]
    names_json = json.dumps(toggl_names)
    day_opts_js = json.dumps([
        [f"{'dom' if k == 'day_of_month' else 'weekday'}:{v}", label]
        for label, k, v in BILLING_REMINDER_DAY_OPTIONS
    ])
    return f"""
    <div class="settings-panel-title">Billing Reminders</div>
    <div class="settings-help">
        Local notifications sent while the app is running. Use 24-hour time
        (e.g. <code>14:00</code>). Notifications post as \u201cPython\u201d; a macOS
        Focus will suppress them unless Python is in that focus\u2019s allowed apps.
    </div>
    <button class="settings-add" type="button" onclick="addBillingReminderRow()">
        + Add reminder
    </button>
    <div id="billing_reminder_rows"
         data-toggl-names='{_esc(names_json)}'
         data-day-options='{_esc(day_opts_js)}'>
        {"".join(rows)}
    </div>
    <div style="margin-top:12px;">
        <button class="settings-btn" type="button"
                onclick="postAction('settings:test_notification')">
            Send Test Notification
        </button>
    </div>
    """


def _render_mapping_row(project_name, customer_id, upwork_id, toggl_names):
    names = list(toggl_names)
    if project_name and project_name not in names:
        names.insert(0, project_name)
    name_opts = ['<option value="">\u2014</option>']
    for n in names:
        sel = " selected" if n == project_name else ""
        name_opts.append(f'<option value="{_esc(n)}"{sel}>{_esc(n)}</option>')

    customer_opts = ['<option value="">\u2014</option>']
    if customer_id:
        # Saved id isn't in the customer list until Refresh fires. Inject a
        # fallback option so the row round-trips cleanly if user doesn't refresh.
        customer_opts.append(
            f'<option value="{_esc(customer_id)}" selected>{_esc(customer_id)}</option>'
        )

    return f"""
    <div class="settings-row map-row" data-row-type="mapping">
        <div class="settings-row-header">
            <select class="map-project">{"".join(name_opts)}</select>
            <select class="map-customer" data-saved-customer-id="{_esc(customer_id or '')}">
                {"".join(customer_opts)}
            </select>
            <input type="text" class="map-upwork" inputmode="numeric"
                   value="{_esc(upwork_id or '')}" placeholder="Upwork contract id">
            <button class="settings-row-remove" type="button"
                    aria-label="Remove" onclick="removeSettingsRow(this)">\u00d7</button>
        </div>
    </div>
    """


def _render_panel_integrations(prefs, integrations):
    token = integrations.get("TOGGL_API_TOKEN", "") or ""
    workspace = integrations.get("TOGGL_WORKSPACE_ID", "") or ""
    stripe_key = integrations.get("STRIPE_API_KEY", "") or ""

    toggl_names = get_toggl_project_names()
    stripe_map = prefs.get("stripe_project_customers") or {}
    upwork_map = prefs.get("upwork_contracts") or {}
    # Union of both maps so rows persist info from either side.
    all_names = set(stripe_map.keys()) | set(upwork_map.keys())
    row_entries = sorted(
        (name, stripe_map.get(name, ""), upwork_map.get(name, ""))
        for name in all_names
    )
    rows_html = "".join(
        _render_mapping_row(n, cid, uid, toggl_names) for n, cid, uid in row_entries
    )
    names_json = json.dumps(toggl_names)

    def field(label_text, key, value, kind):
        # kind: "text" or "password" (masked). Show/hide toggles the type.
        input_type = "text" if kind == "text" else "password"
        return f"""
        <div class="settings-field creds-field">
            <label for="{key}">{_esc(label_text)}</label>
            <input type="{input_type}" id="{key}" value="{_esc(value)}"
                   autocomplete="off" spellcheck="false"
                   autocorrect="off" autocapitalize="off">
            {'<button type="button" class="creds-eye" onclick="toggleCredsVisibility(this)" aria-label="Show/Hide">&#x1F441;</button>' if kind == 'password' else ''}
        </div>
        """

    return f"""
    <div class="settings-panel-title">Integration Credentials</div>
    <div class="settings-help">
        Credentials are stored in the repo-local <code>.env</code> file, not in
        <code>preferences.json</code>. Missing Toggl token blocks save; Stripe key
        must start with <code>sk_</code>.
    </div>
    {field("Toggl API Token", "set_toggl_token", token, "password")}
    {field("Toggl Workspace ID", "set_toggl_workspace", workspace, "text")}
    {field("Stripe API Key", "set_stripe_key", stripe_key, "password")}

    <div class="settings-panel-title" style="margin-top:18px;">Project Billing Mapping</div>
    <div class="settings-help">
        Attach a Toggl project to a Stripe customer and/or Upwork contract. The
        dashboard\u2019s Create Stripe Invoice flow reuses the customer mapping; the
        Open Upwork Diary shortcut reuses the contract id.
    </div>
    <div style="margin-bottom:8px;">
        <button class="settings-btn" type="button" onclick="settingsRefreshStripe()">
            Refresh Customers
        </button>
    </div>
    <button class="settings-add" type="button" onclick="addMappingRow()">
        + Add mapping
    </button>
    <div id="mapping_rows" data-toggl-names='{_esc(names_json)}'>
        {rows_html}
    </div>
    """


def _render_panel_advanced():
    return """
    <div class="settings-panel-title">Advanced</div>
    <div class="settings-help">Advanced tools for troubleshooting and diagnostics.</div>
    <button class="settings-btn" type="button"
            onclick="postAction('settings:open_audit_log')">View API Audit Log</button>
    """


def _render_panel_stub(key, label):
    return (
        f'<div class="settings-panel-title">{_esc(label)}</div>'
        f'<div class="settings-help">This tab will be populated in a later commit.</div>'
    )


def _render_tab_panels(prefs, integrations):
    panels = []
    for idx, (key, label) in enumerate(TAB_ORDER):
        active = " active" if idx == 0 else ""
        if key == "caching":
            body = _render_panel_caching(prefs)
        elif key == "work":
            body = _render_panel_work(prefs)
        elif key == "projects":
            body = _render_panel_projects(prefs)
        elif key == "billing":
            body = _render_panel_billing(prefs)
        elif key == "integrations":
            body = _render_panel_integrations(prefs, integrations)
        elif key == "advanced":
            body = _render_panel_advanced()
        else:
            body = _render_panel_stub(key, label)
        panels.append(
            f'<div class="settings-panel{active}" data-panel="{key}" id="settingsPanel_{key}">'
            f'{body}</div>'
        )
    return "".join(panels)


def generate_settings_html(prefs=None, integrations=None):
    """Return the settings-view HTML fragment (excluding <style>/<script>)."""
    prefs = prefs if prefs is not None else {}
    integrations = integrations if integrations is not None else {}
    tabs_html = _render_tab_nav()
    panels_html = _render_tab_panels(prefs, integrations)
    return f"""
    <div class="settings-root">
        <div class="settings-header">
            <button class="settings-back" aria-label="Back to dashboard"
                    onclick="settingsGoBack()">←</button>
            <div class="settings-title">Preferences</div>
            <div class="settings-actions">
                <button class="settings-btn" onclick="settingsReset()">Reset</button>
                <button class="settings-btn" onclick="settingsCancel()">Cancel</button>
                <button class="settings-btn primary" id="settingsSaveBtn"
                        onclick="settingsSave()">Save</button>
            </div>
        </div>
        <div class="settings-errors" id="settingsErrors"></div>
        {tabs_html}
        <div class="settings-body">
            {panels_html}
        </div>
        <div class="settings-toast" id="settingsToast">Saved ✓</div>
    </div>
    """


# ---------- JS ----------
# Returned with single braces; callers embed this via an f-string substitution,
# so the interpolator preserves JS object/block braces literally.

def generate_settings_js():
    return """
    var __settingsIntegrationsLoaded = false;

    function settingsSelectTab(key) {
        var tabs = document.querySelectorAll('.settings-tab');
        for (var i = 0; i < tabs.length; i++) {
            tabs[i].classList.toggle('active', tabs[i].getAttribute('data-tab') === key);
        }
        var panels = document.querySelectorAll('.settings-panel');
        for (var j = 0; j < panels.length; j++) {
            panels[j].classList.toggle('active', panels[j].getAttribute('data-panel') === key);
        }
        if (key === 'integrations' && !__settingsIntegrationsLoaded) {
            __settingsIntegrationsLoaded = true;
            settingsRefreshStripe();
        }
    }

    function settingsGoBack() {
        document.body.setAttribute('data-view', 'dashboard');
        postAction('router:dashboard');
    }

    function settingsOpen() {
        document.body.setAttribute('data-view', 'settings');
        postAction('router:settings');
    }

    function settingsCancel() {
        settingsGoBack();
    }

    function settingsReset() {
        if (!window.confirm('Reset all settings to defaults? You will still need to click Save to persist.')) {
            return;
        }
        postAction('settings:reset');
    }

    function settingsReadInt(id, fallback) {
        var el = document.getElementById(id);
        if (!el) return fallback;
        var v = parseInt(el.value, 10);
        return isNaN(v) ? fallback : v;
    }

    function settingsCollectProjectTargets() {
        var out = {};
        var rows = document.querySelectorAll('[data-row-type="project-target"]');
        for (var i = 0; i < rows.length; i++) {
            var nameEl = rows[i].querySelector('.pt-name');
            var hoursEl = rows[i].querySelector('.pt-hours');
            var name = nameEl ? (nameEl.value || '').trim() : '';
            if (!name) continue;
            var hoursNum = hoursEl ? parseInt(hoursEl.value, 10) : 0;
            if (isNaN(hoursNum) || hoursNum < 0) hoursNum = 0;
            out[name] = hoursNum;
        }
        return out;
    }

    function settingsCollectProjectRows() {
        var out = [];
        var rows = document.querySelectorAll('[data-row-type="project-defn"]');
        for (var i = 0; i < rows.length; i++) {
            var row = rows[i];
            var nameEl = row.querySelector('.pd-name');
            var typeEl = row.querySelector('.pd-type');
            if (!nameEl || !typeEl) continue;
            var name = (nameEl.value || '').trim();
            if (!name) continue;
            var readVal = function(cls) {
                var el = row.querySelector(cls);
                return el ? (el.value || '').trim() : '';
            };
            out.push({
                name: name,
                type: typeEl.value,
                monthly_amount: readVal('.pd-monthly'),
                target_hours: readVal('.pd-target'),
                hourly_rate: readVal('.pd-rate'),
                cap_hours: readVal('.pd-cap'),
                last_billed_date: readVal('.pd-last-billed'),
                carryover: readVal('.pd-carryover')
            });
        }
        return out;
    }

    function settingsCollectReminderRows() {
        var out = [];
        var rows = document.querySelectorAll('[data-row-type="billing-reminder"]');
        for (var i = 0; i < rows.length; i++) {
            var row = rows[i];
            var enabledEl = row.querySelector('.br-enabled');
            var projectEl = row.querySelector('.br-project');
            var dayEl = row.querySelector('.br-day');
            var timeEl = row.querySelector('.br-time');
            var enabled = !!(enabledEl && enabledEl.checked);
            var project = projectEl ? (projectEl.value || '').trim() : '';
            var time = timeEl ? (timeEl.value || '').trim() : '';
            var dayKey = dayEl ? dayEl.value : '';
            // Drop rule mirrors native: only skip when ALL of project blank, time blank, enabled off.
            if (!project && !time && !enabled) continue;
            out.push({
                enabled: enabled,
                project_name: project,
                task: 'invoice',
                day_key: dayKey,
                time: time
            });
        }
        return out;
    }

    function settingsCollectMappingRows() {
        var out = [];
        var rows = document.querySelectorAll('[data-row-type="mapping"]');
        for (var i = 0; i < rows.length; i++) {
            var row = rows[i];
            var projEl = row.querySelector('.map-project');
            var custEl = row.querySelector('.map-customer');
            var upEl = row.querySelector('.map-upwork');
            var project = projEl ? (projEl.value || '').trim() : '';
            if (!project) continue;
            var customer = custEl ? (custEl.value || '').trim() : '';
            var upwork = upEl ? (upEl.value || '').trim() : '';
            if (!customer && !upwork) continue;
            out.push({ project_name: project, customer_id: customer, upwork_contract_id: upwork });
        }
        return out;
    }

    function settingsReadField(id) {
        var el = document.getElementById(id);
        return el ? (el.value || '').trim() : '';
    }

    function settingsCollectIntegrations() {
        return {
            TOGGL_API_TOKEN: settingsReadField('set_toggl_token'),
            TOGGL_WORKSPACE_ID: settingsReadField('set_toggl_workspace'),
            STRIPE_API_KEY: settingsReadField('set_stripe_key')
        };
    }

    function settingsCollectForm() {
        return {
            cache_ttl_projects: settingsReadInt('set_cache_ttl_projects', 0),
            cache_ttl_today: settingsReadInt('set_cache_ttl_today', 0),
            vacation_days_per_month: settingsReadInt('set_vacation_days', 0),
            project_targets: settingsCollectProjectTargets(),
            projects_rows: settingsCollectProjectRows(),
            billing_reminders_rows: settingsCollectReminderRows(),
            mapping_rows: settingsCollectMappingRows(),
            integrations: settingsCollectIntegrations()
        };
    }

    function toggleCredsVisibility(btn) {
        if (!btn) return;
        var input = btn.parentElement ? btn.parentElement.querySelector('input') : null;
        if (!input) return;
        var show = input.type === 'password';
        input.type = show ? 'text' : 'password';
        btn.classList.toggle('on', show);
    }

    function settingsRefreshStripe() {
        var key = settingsReadField('set_stripe_key');
        postAction('settings:refresh_stripe ' + JSON.stringify({ api_key: key }));
    }

    function onStripeCustomersLoaded(customers) {
        var rows = document.querySelectorAll('.map-customer');
        for (var i = 0; i < rows.length; i++) {
            var sel = rows[i];
            var saved = sel.getAttribute('data-saved-customer-id') || sel.value || '';
            var opts = '<option value="">\u2014</option>';
            var savedPresent = false;
            for (var c = 0; c < customers.length; c++) {
                var cust = customers[c];
                var label = (cust.display_name || cust.id) + ' (' + cust.id + ')';
                var selAttr = (cust.id === saved) ? ' selected' : '';
                if (cust.id === saved) savedPresent = true;
                opts += '<option value="' + cust.id.replace(/"/g, '&quot;') + '"' + selAttr +
                        '>' + label.replace(/</g, '&lt;') + '</option>';
            }
            if (saved && !savedPresent) {
                opts += '<option value="' + saved.replace(/"/g, '&quot;') + '" selected>' +
                        saved + '</option>';
            }
            sel.innerHTML = opts;
        }
    }

    function addMappingRow() {
        var container = document.getElementById('mapping_rows');
        if (!container) return;
        var names = [];
        try {
            names = JSON.parse(container.getAttribute('data-toggl-names') || '[]') || [];
        } catch (_) {}
        var nameOpts = '<option value="">\u2014</option>';
        for (var i = 0; i < names.length; i++) {
            nameOpts += '<option value="' + names[i].replace(/"/g, '&quot;') + '">' + names[i] + '</option>';
        }
        var row = document.createElement('div');
        row.className = 'settings-row map-row';
        row.setAttribute('data-row-type', 'mapping');
        row.innerHTML =
            '<div class="settings-row-header">' +
            '<select class="map-project">' + nameOpts + '</select>' +
            '<select class="map-customer" data-saved-customer-id="">' +
                '<option value="">\u2014</option>' +
            '</select>' +
            '<input type="text" class="map-upwork" inputmode="numeric"' +
            ' placeholder="Upwork contract id">' +
            '<button class="settings-row-remove" type="button" aria-label="Remove"' +
            ' onclick="removeSettingsRow(this)">\u00d7</button>' +
            '</div>';
        container.insertBefore(row, container.firstChild);
    }

    function removeSettingsRow(btn) {
        if (!btn) return;
        var row = btn.closest ? btn.closest('.settings-row') : null;
        if (row && row.parentNode) row.parentNode.removeChild(row);
    }

    function onProjectTypeChange(select) {
        var row = select && select.closest ? select.closest('.pd-row') : null;
        if (!row) return;
        row.setAttribute('data-type', select.value);
    }

    function onLastBilledChange(input) {
        var row = input && input.closest ? input.closest('.pd-row') : null;
        if (!row) return;
        var hasLbd = (input.value || '').trim() ? '1' : '0';
        row.setAttribute('data-has-last-billed', hasLbd);
    }

    function addProjectDefnRow() {
        var container = document.getElementById('project_defn_rows');
        if (!container) return;
        var names = [];
        try {
            names = JSON.parse(container.getAttribute('data-toggl-names') || '[]') || [];
        } catch (_) { names = []; }
        var opts = '<option value="">\u2014</option>';
        for (var i = 0; i < names.length; i++) {
            opts += '<option value="' + names[i].replace(/"/g, '&quot;') + '">' + names[i] + '</option>';
        }
        var types = [
            ['hourly', 'Hourly'],
            ['hourly_with_cap', 'Hourly with cap'],
            ['fixed_required', 'Fixed + required hours'],
            ['fixed_soft', 'Fixed + soft target'],
            ['fixed_flat', 'Fixed flat (no tracking)']
        ];
        var typeOpts = '';
        for (var t = 0; t < types.length; t++) {
            typeOpts += '<option value="' + types[t][0] + '">' + types[t][1] + '</option>';
        }
        var row = document.createElement('div');
        row.className = 'settings-row pd-row';
        row.setAttribute('data-row-type', 'project-defn');
        row.setAttribute('data-type', 'hourly');
        row.setAttribute('data-has-last-billed', '0');
        row.innerHTML =
            '<div class="settings-row-header">' +
            '<select class="pd-name">' + opts + '</select>' +
            '<select class="pd-type" onchange="onProjectTypeChange(this)">' + typeOpts + '</select>' +
            '<button class="settings-row-remove" type="button" aria-label="Remove"' +
            ' onclick="removeSettingsRow(this)">\u00d7</button>' +
            '</div>' +
            '<div class="settings-field pd-field pd-monthly-field"><label>Monthly $</label>' +
            '<input type="text" inputmode="decimal" class="pd-monthly" placeholder="0"></div>' +
            '<div class="settings-field pd-field pd-target-field"><label>Target h</label>' +
            '<input type="text" inputmode="decimal" class="pd-target" placeholder="0"></div>' +
            '<div class="settings-field pd-field pd-rate-field"><label>Rate $/h</label>' +
            '<input type="text" inputmode="decimal" class="pd-rate" placeholder="0"></div>' +
            '<div class="settings-field pd-field pd-cap-field"><label>Cap h</label>' +
            '<input type="text" inputmode="decimal" class="pd-cap" placeholder="0"></div>' +
            '<div class="settings-field pd-field pd-last-billed-field"><label>Last billed</label>' +
            '<input type="date" class="pd-last-billed" onchange="onLastBilledChange(this)"></div>' +
            '<div class="settings-field pd-field pd-carryover-field"><label>Carryover h</label>' +
            '<input type="text" inputmode="decimal" class="pd-carryover" placeholder="0"></div>';
        container.insertBefore(row, container.firstChild);
    }

    function addBillingReminderRow() {
        var container = document.getElementById('billing_reminder_rows');
        if (!container) return;
        var names = [];
        var dayOptions = [];
        try {
            names = JSON.parse(container.getAttribute('data-toggl-names') || '[]') || [];
            dayOptions = JSON.parse(container.getAttribute('data-day-options') || '[]') || [];
        } catch (_) {}
        var nameOpts = '<option value="">\u2014</option>';
        for (var i = 0; i < names.length; i++) {
            nameOpts += '<option value="' + names[i].replace(/"/g, '&quot;') + '">' + names[i] + '</option>';
        }
        var dayOpts = '';
        for (var d = 0; d < dayOptions.length; d++) {
            var key = dayOptions[d][0], label = dayOptions[d][1];
            var sel = (key === 'weekday:friday') ? ' selected' : '';
            dayOpts += '<option value="' + key + '"' + sel + '>' + label + '</option>';
        }
        var row = document.createElement('div');
        row.className = 'settings-row br-row';
        row.setAttribute('data-row-type', 'billing-reminder');
        row.innerHTML =
            '<div class="settings-row-header">' +
            '<label class="br-toggle"><input type="checkbox" class="br-enabled"><span>Enabled</span></label>' +
            '<select class="br-project">' + nameOpts + '</select>' +
            '<select class="br-day">' + dayOpts + '</select>' +
            '<input type="text" class="br-time" placeholder="14:00" maxlength="5">' +
            '<button class="settings-row-remove" type="button" aria-label="Remove"' +
            ' onclick="removeSettingsRow(this)">\u00d7</button>' +
            '</div>';
        container.insertBefore(row, container.firstChild);
    }

    function addProjectTargetRow(name, hours) {
        var container = document.getElementById('project_targets_rows');
        if (!container) return;
        var row = document.createElement('div');
        row.className = 'settings-row';
        row.setAttribute('data-row-type', 'project-target');
        row.innerHTML =
            '<div class="settings-row-header">' +
            '<input type="text" class="pt-name" placeholder="Project name">' +
            '<input type="number" class="pt-hours" min="0" placeholder="0" style="flex:0 0 90px;">' +
            '<button class="settings-row-remove" aria-label="Remove" type="button"' +
            ' onclick="removeSettingsRow(this)">\u00d7</button>' +
            '</div>';
        if (name) row.querySelector('.pt-name').value = name;
        if (hours !== undefined && hours !== null) row.querySelector('.pt-hours').value = hours;
        container.insertBefore(row, container.firstChild);
        var focusEl = row.querySelector('.pt-name');
        if (focusEl) focusEl.focus();
    }

    function settingsShowErrors(errors) {
        var box = document.getElementById('settingsErrors');
        if (!box) return;
        if (!errors || !errors.length) {
            box.classList.remove('show');
            box.innerHTML = '';
            return;
        }
        var shown = errors.slice(0, 5);
        var items = shown.map(function(e) { return '<li>' + String(e).replace(/</g,'&lt;') + '</li>'; }).join('');
        var extra = (errors.length > 5) ? '<div>... and ' + (errors.length - 5) + ' more errors</div>' : '';
        box.innerHTML = '<div><strong>Invalid Preferences</strong></div><ul>' + items + '</ul>' + extra;
        box.classList.add('show');
        if (box.scrollIntoView) {
            box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
    }

    function settingsShowSavedFlash() {
        var toast = document.getElementById('settingsToast');
        var btn = document.getElementById('settingsSaveBtn');
        if (toast) {
            toast.classList.add('show');
            window.setTimeout(function() { toast.classList.remove('show'); }, 1500);
        }
        if (btn) {
            var original = btn.textContent;
            btn.textContent = 'Saved \u2713';
            btn.disabled = true;
            window.setTimeout(function() {
                btn.textContent = original || 'Save';
                btn.disabled = false;
            }, 1500);
        }
    }

    window.__settingsAck = function(reply) {
        if (!reply) return;
        if (reply.type === 'stripe_customers') {
            onStripeCustomersLoaded(reply.customers || []);
            return;
        }
        if (reply.ok) {
            settingsShowErrors([]);
            settingsShowSavedFlash();
            return;
        }
        settingsShowErrors(reply.errors || ['Unknown error']);
    };

    function settingsSave() {
        var payload = settingsCollectForm();
        postAction('settings:save ' + JSON.stringify(payload));
    }
    """
