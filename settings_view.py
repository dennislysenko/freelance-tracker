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

import gusto_sync
import mcp_support

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

    /* --- Integrations grid (cells) + drill-in detail --- */
    .intg-group-title {
        font-size: 12px;
        font-weight: 700;
        color: #c9d1d9;
        margin: 14px 0 2px;
    }
    .intg-group-title:first-child { margin-top: 0; }
    .intg-group-sub {
        font-size: 11px;
        color: #8b949e;
        margin-bottom: 8px;
    }

    .intg-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 8px;
        margin-bottom: 4px;
    }
    /* A lone cell in a group reads as a dangling half-row in two columns. */
    .intg-grid.single { grid-template-columns: 1fr; }

    .intg-cell {
        display: block;
        width: 100%;
        text-align: left;
        padding: 9px 10px;
        background: rgba(255,255,255,0.03);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 8px;
        cursor: pointer;
        -webkit-appearance: none;
        color: inherit;
        font: inherit;
    }
    .intg-cell:hover {
        background: rgba(255,255,255,0.06);
        border-color: rgba(255,255,255,0.16);
    }
    .intg-cell.active {
        background: rgba(63,185,80,0.10);
        border-color: rgba(63,185,80,0.28);
    }

    .intg-cell-top {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 6px;
    }
    .intg-cell-name {
        font-size: 12px;
        font-weight: 600;
        color: #c9d1d9;
    }
    .intg-cell-icon {
        font-size: 13px;
        opacity: 0.75;
        flex: none;
    }
    .intg-cell-status {
        font-size: 11px;
        color: #8b949e;
        margin-top: 2px;
    }
    .intg-cell.active .intg-cell-status { color: #3fb950; }

    /* Grid and detail are mutually exclusive; JS flips data-intg on the
       panel so reopening the popover can restore either state. */
    .settings-panel[data-intg=""] .intg-detail { display: none; }
    .settings-panel:not([data-intg=""]) .intg-index { display: none; }
    .intg-detail { display: none; }
    .intg-detail.active { display: block; }

    .mcp-toggle { display: flex; margin: 8px 0; }
    .gusto-row { display: flex; align-items: center; gap: 8px; margin: 8px 0; font-size: 12px; }
    .gusto-row select { flex: 1; }
    .gusto-status { font-size: 11px; color: #8b949e; margin: 4px 0; line-height: 1.5; }
    .gusto-status strong { color: #c9d1d9; font-weight: 600; }
    .gusto-status.warn { color: #f0883e; }
    .mcp-regs {
        display: flex;
        align-items: center;
        gap: 8px;
        margin: 10px 0 6px;
        font-size: 11px;
    }
    .mcp-reg {
        color: #8b949e;
        border: 1px solid rgba(255,255,255,0.10);
        border-radius: 999px;
        padding: 2px 8px;
    }
    .mcp-reg.on { color: #3fb950; border-color: rgba(63,185,80,0.35); }
    .mcp-actions { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0 8px; }
    .mcp-snippet {
        display: block;
        width: 100%;
        box-sizing: border-box;
        margin: 0 0 6px;
        padding: 6px 8px;
        font: 10px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace;
        color: #8b949e;
        background: rgba(0,0,0,0.25);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 6px;
        resize: none;
        white-space: pre;
        overflow-x: auto;
    }

    .intg-detail-head {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 10px;
    }
    .intg-back {
        background: transparent;
        border: 0;
        color: #8b949e;
        font-size: 15px;
        line-height: 1;
        cursor: pointer;
        padding: 2px 4px;
        border-radius: 5px;
        -webkit-appearance: none;
    }
    .intg-back:hover { color: #c9d1d9; background: rgba(255,255,255,0.06); }
    .intg-detail-title {
        font-size: 12px;
        font-weight: 700;
        color: #c9d1d9;
    }
    .intg-detail-badge {
        font-size: 10px;
        color: #3fb950;
        margin-left: auto;
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
        top: 10px;
        padding: 6px 10px;
        border-radius: 8px;
        background: rgba(63,185,80,0.18);
        color: #3fb950;
        border: 1px solid rgba(63,185,80,0.36);
        font-size: 11px;
        line-height: 1;
        opacity: 0;
        transition: opacity 0.2s ease;
        z-index: 30;
        pointer-events: none;
    }
    .settings-toast.show { opacity: 1; }
    .settings-btn.saved-hidden { visibility: hidden; }

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

def _render_tab_nav(active_tab=None):
    buttons = []
    valid = {k for k, _ in TAB_ORDER}
    active_tab = active_tab if active_tab in valid else TAB_ORDER[0][0]
    for idx, (key, label) in enumerate(TAB_ORDER):
        cls = "settings-tab" + (" active" if key == active_tab else "")
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
    from datetime import datetime
    vac = int(prefs.get("vacation_days_per_month", 4) or 0)
    keywords_str = ", ".join(prefs.get("days_off_keywords") or [])
    _this_month = datetime.now().strftime("%Y-%m")
    rev_share = prefs.get("monthly_rev_share", {}).get(_this_month, 0) or 0
    rev_share_val = int(rev_share) if rev_share == int(rev_share) else rev_share
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
    <div class="settings-field">
        <label for="set_days_off_keywords">Days Off Keywords</label>
        <input type="text" id="set_days_off_keywords" value="{_esc(keywords_str)}"
               spellcheck="false" autocorrect="off" autocapitalize="off">
    </div>
    <div class="settings-help">
        With a Google Calendar ICS URL set (<strong>Integrations</strong> tab), business
        days whose calendar events match one of these comma-separated keywords
        (case-insensitive, recurring events included) are excluded from the month
        projection. Vacation Days/Month is only the fallback when no calendar is
        configured or it can’t be fetched.
    </div>
    <div class="settings-field">
        <label for="set_rev_share">This Month's Rev Share</label>
        <input type="number" id="set_rev_share" min="0" step="1" value="{rev_share_val}">
    </div>
    <div class="settings-help">
        Variable rev-share income for the current month (<code>{_this_month}</code>),
        added to this month's projection as guaranteed income. It is stored per month,
        so next month starts blank — set it again when you know the new amount.
        Enter <code>0</code> to clear it.
    </div>
    <div class="settings-help">
        Monthly hour goals for <code>hourly</code> projects and projects without a
        billing definition. For <code>fixed</code> or <code>hourly with cap</code> projects,
        set the target in the <strong>Projects</strong> tab instead \u2014 a value here
        silently overrides it. Blank-name rows are dropped on save.
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


def _render_panel_integrations(prefs, integrations, active_integration=None):
    """Render Integrations as a grid of cells that drill into detail panes.

    Each credential used to be a field in one long scrolling column, which meant
    hunting for the right one every time the popover reopened. Now the panel
    shows compact status cells; clicking one swaps the grid for just that
    integration's fields.
    """
    token = integrations.get("TOGGL_API_TOKEN", "") or ""
    workspace = integrations.get("TOGGL_WORKSPACE_ID", "") or ""
    stripe_key = integrations.get("STRIPE_API_KEY", "") or ""
    calendar_url = integrations.get("GOOGLE_CALENDAR_ICS_URL", "") or ""
    openai_key = integrations.get("OPENAI_API_KEY", "") or ""

    toggl_names = get_toggl_project_names()
    stripe_map = prefs.get("stripe_project_customers") or {}
    upwork_map = prefs.get("upwork_contracts") or {}
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

    toggl_detail = f"""
    {field("Toggl API Token", "set_toggl_token", token, "password")}
    {field("Toggl Workspace ID", "set_toggl_workspace", workspace, "text")}
    <div class="settings-help">
        Required. Everything else in this app reads from Toggl; without a token
        the dashboard cannot load. Find your token at
        <code>track.toggl.com/profile</code>.
    </div>
    """

    openai_detail = f"""
    <div class="settings-help">
        Optional. Bring your own OpenAI key to type entries in plain English
        (&ldquo;1 hour of Acme retainer at 9am the past 2 days, no note&rdquo;) and
        ask questions about your hours. You always confirm proposed entries before
        anything is written to Toggl. Billed to <em>your</em> OpenAI account &mdash;
        expect well under $1/month.
    </div>
    {field("OpenAI API Key", "set_openai_key", openai_key, "password")}
    <div class="settings-help">
        <strong>Privacy:</strong> when you use this feature, what you type plus your
        Toggl <em>project names</em> are sent to OpenAI under your own account and
        its data-retention terms. Project names are often client names. Nothing is
        sent unless you use the feature, and no other credential ever leaves your Mac.
    </div>
    <div class="settings-help">
        <strong>Setting up a tightly scoped key:</strong>
        <ol style="margin:6px 0 0 16px; padding:0;">
            <li>At platform.openai.com, create a <strong>new project</strong>
                (Settings &rarr; Projects) so this app is isolated from your other work.</li>
            <li>In that project&rsquo;s <strong>Limits</strong>, set a hard monthly budget
                &mdash; $5 is generous &mdash; and an email alert. This caps the damage
                if the key ever leaks.</li>
            <li>Under <strong>API keys</strong>, create a key owned by that project
                (not a user or service-account key) with <strong>Restricted</strong>
                permissions. Set <em>Model capabilities</em> to <strong>Request</strong>
                &mdash; that grants Write on <code>/v1/responses</code>, which is the
                only endpoint this app calls &mdash; and leave everything else
                (Assistants, Files, Fine-tuning, Vector stores, Admin) on
                <em>None</em>.</li>
            <li>Paste it above. Saving verifies the key before it is stored.</li>
        </ol>
    </div>
    <button class="settings-btn" type="button"
            onclick="postAction('settings:open_openai_settings')">Open OpenAI API Keys</button>
    """

    stripe_detail = f"""
    <div class="settings-help">
        Optional. Powers the dashboard&rsquo;s <strong>Create Stripe Invoice</strong>
        flow, which creates <em>draft</em> invoices only &mdash; you review and send
        them yourself in Stripe.
    </div>
    {field("Stripe API Key", "set_stripe_key", stripe_key, "password")}
    <div class="settings-help">Must start with <code>sk_</code>.</div>
    """

    calendar_detail = f"""
    <div class="settings-help">
        Optional. Lets the month projection count your <em>actual</em> days off
        instead of a flat vacation estimate.
    </div>
    {field("Google Calendar ICS URL", "set_calendar_ics_url", calendar_url, "password")}
    <div class="settings-help">
        In Google Calendar settings, under <strong>Settings for my calendars</strong>
        (left sidebar), choose the calendar you put your vacation days on, then
        scroll to <strong>Integrate calendar</strong> &rarr;
        <em>Secret address in iCal format</em> and copy it here. Which events count
        is controlled by <strong>Work Planning</strong> &rarr; Days Off Keywords.
        Fetched at most every 6 hours.
    </div>
    <button class="settings-btn" type="button"
            onclick="postAction('settings:open_gcal_settings')">Open Google Calendar Settings</button>
    """

    mapping_detail = f"""
    <div class="settings-help">
        Attach a Toggl project to a Stripe customer and/or Upwork contract. The
        dashboard&rsquo;s Create Stripe Invoice flow reuses the customer mapping; the
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

    mcp_enabled = bool(prefs.get("mcp_enabled", False))
    mcp_anonymize = bool(prefs.get("mcp_anonymize", False))
    mcp_write = bool(prefs.get("mcp_write_enabled", False))
    regs = mcp_support.detect_registrations()
    claude_cmd = mcp_support.claude_code_command()
    codex_block = mcp_support.codex_config_block()

    def reg_badge(label, present):
        mark = "&#10003;" if present else "&#10007;"
        cls = " on" if present else ""
        return f'<span class="mcp-reg{cls}">{mark} {label}</span>'

    mcp_detail = f"""
    <div class="settings-help">
        Lets coding agents (Claude Code, Codex, anything that speaks MCP) read your
        hours, earnings, the month projection, per-project pacing, and your billing
        rules, so you can ask &ldquo;how is my month going and where should I put
        more time?&rdquo; and get an answer from real numbers.
        <strong>Reading is the default</strong>; logging and editing time is a
        separate switch below.
    </div>
    <label class="br-toggle mcp-toggle">
        <input type="checkbox" id="set_mcp_enabled"{' checked' if mcp_enabled else ''}>
        <span>Enable MCP server</span>
    </label>
    <label class="br-toggle mcp-toggle">
        <input type="checkbox" id="set_mcp_write_enabled"{' checked' if mcp_write else ''}>
        <span>Allow agents to log and edit time</span>
    </label>
    <div class="settings-help">
        Off by default. When on, an agent can log a new entry, edit one, or delete
        one &mdash; <strong>one entry per request, never in bulk</strong>. Every
        write is two-step: the agent first gets a preview of the exact change and
        has to come back with a confirmation token, so you see what is about to
        happen before it does. Deleting is not undoable from the agent.
    </div>
    <label class="br-toggle mcp-toggle">
        <input type="checkbox" id="set_mcp_anonymize"{' checked' if mcp_anonymize else ''}>
        <span>Anonymize client names and dollar amounts</span>
    </label>
    <div class="settings-help">
        When off, the agent sees your real project names and rates. Turn it on if
        the agent runs on a model you do not want client names sent to. Ratios are
        kept so pacing still makes sense. Anonymizing blocks writing, since an
        agent cannot safely edit hours on a project it cannot name.
    </div>
    <div class="mcp-regs">
        <span class="settings-help" style="margin:0">Registered in:</span>
        {reg_badge("Claude Code", regs["claude_code"])}
        {reg_badge("Codex", regs["codex"])}
    </div>
    <div class="mcp-actions">
        <button class="settings-btn" type="button"
                onclick="settingsCopySnippet('mcp_claude_cmd', this)">Copy Claude Code command</button>
        <button class="settings-btn" type="button"
                onclick="settingsCopySnippet('mcp_codex_toml', this)">Copy Codex config</button>
        <button class="settings-btn" type="button" id="mcpTestBtn"
                onclick="settingsTestMcp()">Test server</button>
    </div>
    <div class="settings-help" id="mcpTestResult"></div>
    <textarea id="mcp_claude_cmd" class="mcp-snippet" readonly rows="2"
              spellcheck="false">{_esc(claude_cmd)}</textarea>
    <textarea id="mcp_codex_toml" class="mcp-snippet" readonly rows="3"
              spellcheck="false">{_esc(codex_block)}</textarea>
    <div class="settings-help">
        Paste the Claude Code command in a terminal, or the TOML block into
        <code>~/.codex/config.toml</code>. Then save here with the server enabled.
        The data an agent reads is exactly what this dashboard shows, as of its last
        refresh; the server never calls Toggl itself. Setup guide:
        <code>docs/mcp-agents.md</code>.
    </div>
    """

    gusto_status = gusto_sync.status_view(prefs, gusto_sync.load_state())
    gusto_enabled = bool(prefs.get("gusto_sync_enabled", False))
    gusto_project = prefs.get("gusto_project") or ""
    gusto_names = list(toggl_names)
    if gusto_project and gusto_project not in gusto_names:
        gusto_names.insert(0, gusto_project)
    gusto_options = '<option value="">Choose a project</option>' + "".join(
        f'<option value="{_esc(n)}"{" selected" if n == gusto_project else ""}>{_esc(n)}</option>'
        for n in gusto_names
    )
    gusto_company = gusto_status["company"]
    gusto_login_line = (
        f'Logged in as <strong>{_esc(gusto_company)}</strong>'
        if gusto_company and not gusto_status["login_required"] else
        '<span class="warn">Not logged in</span>' if not gusto_company else
        '<span class="warn">Gusto login expired</span>'
    )
    gusto_issue_lines = "".join(
        f'<div class="gusto-status warn">&#9888; {_esc(msg)}</div>' for msg in gusto_status["issues"]
    )
    gusto_error_line = (
        f'<div class="gusto-status warn">{_esc(gusto_status["error"])}</div>'
        if gusto_status["error"] else ""
    )
    gusto_detail = f"""
    <div class="settings-help">
        Pushes one project&rsquo;s Toggl hours into your Gusto contractor timesheet as
        shifts on their real dates, with each entry&rsquo;s description as the note.
        Runs Mondays at 9:00 for the previous week, or any time from
        <strong>Export/Invoice &rarr; Push to Gusto</strong>. It only ever adds shifts;
        if you edit or delete an entry in Toggl after it was pushed, it is flagged here
        for you to fix in Gusto.
    </div>
    <label class="br-toggle mcp-toggle">
        <input type="checkbox" id="set_gusto_sync_enabled"{' checked' if gusto_enabled else ''}>
        <span>Push hours to Gusto</span>
    </label>
    <div class="gusto-row">
        <span>Project</span>
        <select id="set_gusto_project">{gusto_options}</select>
    </div>
    <div class="gusto-status">{gusto_login_line} &middot; {_esc(gusto_status["meta"])}</div>
    <div class="gusto-status">Next payday: {_esc(gusto_status["next_payday"])}</div>
    {f'<div class="gusto-status">Last push: {_esc(gusto_status["last_result"])}</div>' if gusto_status["last_result"] else ''}
    {gusto_error_line}
    {gusto_issue_lines}
    <div class="mcp-actions">
        <button class="settings-btn" type="button" id="gustoLoginBtn"
                onclick="settingsGustoLogin()">Log in to Gusto</button>
    </div>
    <div class="settings-help" id="gustoLoginResult"></div>
    <div class="settings-help">
        Gusto opens in its own Chrome window. Log in with your passkey there; the
        window stays open afterwards so you can check or fix anything by hand.
        Gusto&rsquo;s session ends when that window is closed, so a push may ask
        for your passkey again.
    </div>
    """

    # (key, label, icon, configured, detail html) grouped for the index.
    groups = [
        ("Time tracking", "Where your hours come from.", [
            ("toggl", "Toggl", "&#9201;", bool(token and workspace), toggl_detail),
        ]),
        ("Assistant", "Log time by typing it in plain English.", [
            ("openai", "OpenAI", "&#10022;", bool(openai_key), openai_detail),
        ]),
        ("Billing &amp; invoicing", "Turn tracked hours into invoices.", [
            ("stripe", "Stripe", "&#128179;", bool(stripe_key), stripe_detail),
            ("mapping", "Project Mapping", "&#128279;", bool(all_names), mapping_detail),
            ("gusto", "Gusto", "&#128181;", gusto_status["configured"] and bool(gusto_status["company"]),
             gusto_detail),
        ]),
        ("Planning", "Feeds the month projection.", [
            ("calendar", "Google Calendar", "&#128197;", bool(calendar_url), calendar_detail),
        ]),
        ("Agents", "Let Claude Code or Codex read your numbers.", [
            ("mcp", "Agents (MCP)", "&#129302;", mcp_enabled, mcp_detail),
        ]),
    ]

    index_parts = []
    detail_parts = []
    for group_label, group_sub, cells in groups:
        index_parts.append(f'<div class="intg-group-title">{group_label}</div>')
        index_parts.append(f'<div class="intg-group-sub">{group_sub}</div>')
        grid_cls = "intg-grid single" if len(cells) == 1 else "intg-grid"
        index_parts.append(f'<div class="{grid_cls}">')
        for key, label, icon, configured, detail in cells:
            state = " active" if configured else ""
            opened = " active" if key == active_integration else ""
            status = "&#10003; Active" if configured else "Configure integration"
            index_parts.append(
                f'<button class="intg-cell{state}" type="button" data-intg-cell="{key}" '
                f'onclick="settingsOpenIntegration(\'{key}\')">'
                f'<div class="intg-cell-top">'
                f'<span class="intg-cell-name">{label}</span>'
                f'<span class="intg-cell-icon">{icon}</span>'
                f'</div>'
                f'<div class="intg-cell-status">{status}</div>'
                f'</button>'
            )
            badge = '<span class="intg-detail-badge">&#10003; Active</span>' if configured else ""
            detail_parts.append(
                f'<div class="intg-detail{opened}" data-intg-detail="{key}">'
                f'<div class="intg-detail-head">'
                f'<button class="intg-back" type="button" aria-label="Back to integrations" '
                f'onclick="settingsCloseIntegration()">&#8592;</button>'
                f'<span class="intg-detail-title">{label}</span>{badge}'
                f'</div>{detail}</div>'
            )
        index_parts.append('</div>')

    return (
        '<div class="intg-index">' + "".join(index_parts) + '</div>'
        + "".join(detail_parts)
    )


def _render_panel_advanced():
    return """
    <div class="settings-panel-title">Advanced</div>
    <div class="settings-help">Advanced tools for troubleshooting and diagnostics.</div>
    <button class="settings-btn" type="button"
            onclick="postAction('settings:open_audit_log')">View API Audit Log</button>
    <button class="settings-btn" type="button"
            onclick="postAction('copy_diagnostics')">Copy Projection Diagnostics</button>
    <div class="settings-help">
        Copies an <strong>anonymized</strong> snapshot of your projection math to the
        clipboard — config plus the computed month, with project names replaced by
        labels and all rates/amounts normalized (absolute dollars removed, ratios kept).
        Share it to debug why an on-pace estimate looks off. No secrets are included.
    </div>
    """


def _render_panel_stub(key, label):
    return (
        f'<div class="settings-panel-title">{_esc(label)}</div>'
        f'<div class="settings-help">This tab will be populated in a later commit.</div>'
    )


def _render_tab_panels(prefs, integrations, active_tab=None, active_integration=None):
    panels = []
    valid = {k for k, _ in TAB_ORDER}
    active_tab = active_tab if active_tab in valid else TAB_ORDER[0][0]
    for idx, (key, label) in enumerate(TAB_ORDER):
        active = " active" if key == active_tab else ""
        if key == "caching":
            body = _render_panel_caching(prefs)
        elif key == "work":
            body = _render_panel_work(prefs)
        elif key == "projects":
            body = _render_panel_projects(prefs)
        elif key == "billing":
            body = _render_panel_billing(prefs)
        elif key == "integrations":
            body = _render_panel_integrations(prefs, integrations, active_integration)
        elif key == "advanced":
            body = _render_panel_advanced()
        else:
            body = _render_panel_stub(key, label)
        # data-intg drives the grid/detail swap in CSS; rendering it here (not
        # only in JS) is what restores the open integration after a reopen.
        extra = ""
        if key == "integrations":
            extra = f' data-intg="{_esc(active_integration or "")}"'
        panels.append(
            f'<div class="settings-panel{active}" data-panel="{key}" '
            f'id="settingsPanel_{key}"{extra}>'
            f'{body}</div>'
        )
    return "".join(panels)


def generate_settings_html(prefs=None, integrations=None, active_tab=None,
                           active_integration=None):
    """Return the settings-view HTML fragment (excluding <style>/<script>).

    `active_tab` / `active_integration` restore where the user was; the popover
    is transient, so switching to a browser to fetch a credential dismisses it,
    and re-rendering from scratch used to dump them back on the first tab.
    """
    prefs = prefs if prefs is not None else {}
    integrations = integrations if integrations is not None else {}
    tabs_html = _render_tab_nav(active_tab)
    panels_html = _render_tab_panels(prefs, integrations, active_tab, active_integration)
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
        // Mirror to Python so dismissing and reopening the popover comes back
        // to this tab instead of resetting to the first one.
        postAction('settings_tab:' + key);
    }

    function settingsIntegrationsPanel() {
        return document.getElementById('settingsPanel_integrations');
    }

    function settingsApplyIntegration(key) {
        var panel = settingsIntegrationsPanel();
        if (!panel) return;
        panel.setAttribute('data-intg', key || '');
        var details = panel.querySelectorAll('.intg-detail');
        for (var i = 0; i < details.length; i++) {
            details[i].classList.toggle(
                'active', details[i].getAttribute('data-intg-detail') === key
            );
        }
        window.scrollTo(0, 0);
    }

    function settingsOpenIntegration(key) {
        settingsApplyIntegration(key);
        postAction('settings_intg:' + key);
    }

    function settingsCloseIntegration() {
        settingsApplyIntegration('');
        postAction('settings_intg:');
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
            STRIPE_API_KEY: settingsReadField('set_stripe_key'),
            GOOGLE_CALENDAR_ICS_URL: settingsReadField('set_calendar_ics_url'),
            OPENAI_API_KEY: settingsReadField('set_openai_key')
        };
    }

    function settingsCollectDaysOffKeywords() {
        return settingsReadField('set_days_off_keywords')
            .split(',')
            .map(function (s) { return s.trim(); })
            .filter(function (s) { return s.length > 0; });
    }

    function settingsReadBool(id) {
        var el = document.getElementById(id);
        return !!(el && el.checked);
    }

    function settingsCopySnippet(id, btn) {
        var el = document.getElementById(id);
        if (!el) return;
        postAction('copy_text:' + encodeURIComponent(el.value || ''));
        if (btn) {
            var label = btn.textContent;
            btn.textContent = 'Copied \u2713';
            window.setTimeout(function () { btn.textContent = label; }, 1200);
        }
    }

    function settingsTestMcp() {
        var btn = document.getElementById('mcpTestBtn');
        var out = document.getElementById('mcpTestResult');
        if (btn) { btn.disabled = true; btn.textContent = 'Testing\u2026'; }
        if (out) { out.textContent = ''; }
        postAction('settings:test_mcp');
    }

    function settingsGustoLogin() {
        var btn = document.getElementById('gustoLoginBtn');
        var out = document.getElementById('gustoLoginResult');
        if (btn) { btn.disabled = true; btn.textContent = 'Waiting for Gusto\u2026'; }
        if (out) { out.textContent = 'Log in with your passkey in the Gusto window.'; out.style.color = ''; }
        postAction('settings:gusto_login');
    }

    function onGustoLoginResult(reply) {
        var btn = document.getElementById('gustoLoginBtn');
        var out = document.getElementById('gustoLoginResult');
        if (btn) { btn.disabled = false; btn.textContent = 'Log in to Gusto'; }
        if (!out) return;
        out.textContent = reply.ok ? ('Logged in to Gusto (' + reply.company + ')') : (reply.error || 'Login failed');
        out.style.color = reply.ok ? '#3fb950' : '#f85149';
    }

    function onMcpTestResult(reply) {
        var btn = document.getElementById('mcpTestBtn');
        var out = document.getElementById('mcpTestResult');
        if (btn) { btn.disabled = false; btn.textContent = 'Test server'; }
        if (!out) return;
        if (reply.ok) {
            var msg = reply.tools + ' tools available';
            msg += reply.writable ? ' \u00b7 read + write' : ' \u00b7 read-only';
            if (reply.data_as_of) msg += ' \u00b7 data as of ' + reply.data_as_of;
            if (reply.enabled === false) msg += ' \u00b7 server is disabled (save with it enabled to serve data)';
            out.textContent = msg;
            out.style.color = '#3fb950';
        } else {
            out.textContent = reply.error || 'Test failed';
            out.style.color = '#f85149';
        }
    }

    function settingsCollectForm() {
        return {
            gusto_sync_enabled: settingsReadBool('set_gusto_sync_enabled'),
            gusto_project: (document.getElementById('set_gusto_project') || {}).value || '',
            mcp_enabled: settingsReadBool('set_mcp_enabled'),
            mcp_anonymize: settingsReadBool('set_mcp_anonymize'),
            mcp_write_enabled: settingsReadBool('set_mcp_write_enabled'),
            cache_ttl_projects: settingsReadInt('set_cache_ttl_projects', 0),
            cache_ttl_today: settingsReadInt('set_cache_ttl_today', 0),
            vacation_days_per_month: settingsReadInt('set_vacation_days', 0),
            days_off_keywords: settingsCollectDaysOffKeywords(),
            rev_share_this_month: settingsReadInt('set_rev_share', 0),
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
            btn.classList.add('saved-hidden');
            btn.disabled = true;
            window.setTimeout(function() {
                btn.classList.remove('saved-hidden');
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
        if (reply.type === 'mcp_test') {
            onMcpTestResult(reply);
            return;
        }
        if (reply.type === 'gusto_login') {
            onGustoLoginResult(reply);
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
