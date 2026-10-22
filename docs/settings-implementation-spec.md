# Settings Implementation Spec

Last updated: 2026-04-20

> **Implementation status (2026-04-20):** The canonical settings UI is now the in-popover web view implemented in `settings_view.py` + `settings_handler.py` and hosted by `dashboard_panel.py`. The AppKit window in `preferences_window.py` is deprecated and retained only as the fallback path when the WebKit bridge is unavailable.
>
> The storage layout, validation contract, and save/reset/cancel behavior in the sections below are authoritative for both UIs — a save from either path produces byte-identical `preferences.json`, `.env`, and `retainer_carryover.json` output. UI quirks marked "(native)" (fixed 5/4 row grids, plain-text credential fields, validation as a single modal alert) are relaxed in the web view to dynamic rows, masked inputs with a show/hide toggle, and a top-of-panel error list in addition to the modal; none of these changes affect persistence.

This document specifies the settings system exactly as implemented. It is intended to support a 1:1 reimplementation in another stack, including current storage layout, validation, UI behavior, side effects, and quirks.

## Scope

The current settings implementation spans three persisted stores and one cache-backed option source:

1. `~/Library/Application Support/TogglMenuBar/preferences.json`
2. `<repo>/.env`
3. `~/Library/Application Support/TogglMenuBar/retainer_carryover.json`
4. `~/Library/Caches/TogglMenuBar/projects.json` as the source for project dropdown options

Only `preferences.json` and `.env` are primary user settings stores. `retainer_carryover.json` is settings-adjacent state because the Preferences UI both reads and writes it. `projects.json` is not a settings file, but it determines which project names appear in several settings controls.

## Storage Model

### Preferences Store

- Path: `~/Library/Application Support/TogglMenuBar/preferences.json`
- Format: pretty-printed JSON (`indent=2`)
- Bootstrap behavior:
  - The app creates `~/Library/Application Support/TogglMenuBar/` on import.
  - If `preferences.json` does not exist, `load_preferences()` writes the default object to disk immediately, then returns it.
- Read behavior:
  - Load raw JSON.
  - Shallow-merge it over defaults.
  - Special-case `dashboard_sections` to deep-merge nested keys.
  - Preserve unknown top-level keys from disk.
- Failure behavior:
  - If JSON load fails for any reason, return defaults in memory and print an error.
  - Invalid/corrupt JSON is not repaired or overwritten automatically.

### Integrations Store

- Path: `<repo>/.env`
- Format: dotenv-style `KEY=value` lines
- Managed keys:
  - `TOGGL_API_TOKEN`
  - `TOGGL_WORKSPACE_ID`
  - `STRIPE_API_KEY`
- Read behavior:
  - If `.env` exists, read only those three keys from it.
  - If `.env` does not exist, fall back to current process environment variables for those three keys.
- Write behavior:
  - Preserve unknown existing `.env` keys.
  - Replace the three managed keys with the newly supplied values.
  - Mirror managed values into `os.environ`; remove them from `os.environ` if saved blank.

### Carryover Store

- Path: `~/Library/Application Support/TogglMenuBar/retainer_carryover.json`
- Format: JSON object keyed by project name, then `YYYY-MM`
- This store is used by the Projects tab for manual carryover overrides.
- Current normalized record shape:

```json
{
  "Client A": {
    "2026-03": {
      "hours": 2.5,
      "source": "manual",
      "updated_at": "2026-04-16T09:12:33"
    }
  }
}
```

- Legacy scalar values are still accepted on read and normalized in memory to:
  - `hours`: numeric value
  - `source`: `"manual"`
  - `updated_at`: `null`

### Project Options Source

- Path: `~/Library/Caches/TogglMenuBar/projects.json`
- Purpose: drives project dropdown choices in the Preferences window.
- Behavior:
  - If present and parseable, project names are extracted, sorted alphabetically, and used in UI popups.
  - If absent or unreadable, project popups only show the placeholder `—`, plus any already-saved project names injected back into the UI for existing rows.

## Preferences Schema

Exact default object:

```json
{
  "cache_ttl_projects": 86400,
  "cache_ttl_today": 1800,
  "vacation_days_per_month": 4,
  "project_targets": {},
  "retainer_hourly_rates": {},
  "projects": {},
  "stripe_project_customers": {},
  "upwork_contracts": {},
  "billing_reminders": [],
  "dashboard_sections": {
    "today": true,
    "week": false,
    "month": true
  }
}
```

### Field Semantics

| Key | Type | Default | Notes |
|---|---|---:|---|
| `cache_ttl_projects` | integer | `86400` | Required. Positive. |
| `cache_ttl_today` | integer | `1800` | Required. Positive. |
| `vacation_days_per_month` | integer | `4` | Required. Range `0..31`. |
| `project_targets` | object | `{}` | Project name -> numeric monthly hour target. |
| `retainer_hourly_rates` | object | `{}` | Legacy fallback rates. Project name -> positive number. |
| `projects` | object | `{}` | Canonical project definition map. |
| `stripe_project_customers` | object | `{}` | Project name -> Stripe customer id. |
| `upwork_contracts` | object | `{}` | Project name -> numeric-string Upwork contract id. |
| `billing_reminders` | array | `[]` | Reminder objects. |
| `dashboard_sections` | object | `{"today": true, "week": false, "month": true}` | Persisted collapse state for dashboard sections. Not editable in the Preferences window. |

### `projects` Schema

`projects` is keyed by Toggl project name. Valid shapes:

#### Hourly

```json
{
  "billing_type": "hourly"
}
```

No additional fields are stored.

#### Hourly With Cap

```json
{
  "billing_type": "hourly_with_cap",
  "hourly_rate": 150,
  "cap_hours": 20,
  "last_billed_date": "2026-03-15"
}
```

- `hourly_rate`: positive number, required
- `cap_hours`: positive number, required
- `last_billed_date`: optional string matching `YYYY-MM-DD`

#### Fixed Monthly

```json
{
  "billing_type": "fixed_monthly",
  "monthly_amount": 3000,
  "hour_tracking": "required",
  "target_hours": 20
}
```

- `monthly_amount`: positive number, required
- `hour_tracking`: one of `"required"`, `"soft"`, `"none"`
- `target_hours`: required positive number only when `hour_tracking` is `"required"` or `"soft"`

### `billing_reminders` Schema

Reminder objects have two valid scheduling modes.

#### Weekly

```json
{
  "enabled": true,
  "project_name": "Acme Inc",
  "task": "invoice",
  "weekday": "friday",
  "time": "14:00"
}
```

#### Monthly

```json
{
  "enabled": true,
  "project_name": "Acme Inc",
  "task": "invoice",
  "day_of_month": -1,
  "time": "17:00"
}
```

Constraints:

- `enabled`: boolean, optional in validation, defaulted by validator logic to `true`
- `project_name`: required non-empty string
- `task`: must be `"invoice"`
- exactly one effective schedule mode is expected:
  - `weekday`: one of `monday`, `tuesday`, `wednesday`, `thursday`, `friday`, `saturday`, `sunday`
  - or `day_of_month`: one of `1..28`, `-1`, `-2`, `-3`
- `time`: string in `HH:MM` 24-hour format

### `dashboard_sections` Schema

```json
{
  "today": true,
  "week": false,
  "month": true
}
```

- Only valid keys: `today`, `week`, `month`
- All values must be booleans

## Integration Schema

Managed `.env` keys:

```dotenv
TOGGL_API_TOKEN=...
TOGGL_WORKSPACE_ID=...
STRIPE_API_KEY=...
```

Validation performed by the Preferences window before save:

- `TOGGL_API_TOKEN` is required
- `STRIPE_API_KEY` is optional, but if present it must start with `sk_`
- `TOGGL_WORKSPACE_ID` is not validated in the window

## Validation Contract

`validate_preferences()` validates only `preferences.json` content, not `.env`.

### Required Top-Level Fields

- `cache_ttl_projects`: integer > 0
- `cache_ttl_today`: integer > 0
- `vacation_days_per_month`: integer between 0 and 31 inclusive

### Optional Top-Level Fields

- `project_targets`: object; values must be numeric and non-negative
- `retainer_hourly_rates`: object; values must be numeric and > 0
- `projects`: object using the schema above
- `stripe_project_customers`: object; values must be strings and, if non-blank, start with `cus_`
- `upwork_contracts`: object; values must be strings and, if non-blank, digits only
- `billing_reminders`: array of objects using the schema above
- `dashboard_sections`: object with only the three allowed boolean keys

### Important Validation/Serialization Mismatches

These quirks are real and must be preserved for a 1:1 port:

1. `project_targets` validation allows floats, but the native UI saves them as integers because it reads `intValue()`.
2. Project definition numeric fields are plain text inputs. On save, each value is parsed as float, and parse failure silently becomes `0.0` before validation runs.
3. Existing reminder objects without `enabled` validate as enabled by default, but the UI loads missing `enabled` as unchecked/off.

## Preferences Window Contract

The Preferences window is a singleton controller. Only one window instance exists.

### Window Shell

- Title: `Freelance Tracker Preferences`
- Size: `600 x 680`
- Style: titled, closable, miniaturizable
- Bottom buttons:
  - `Reset to Defaults`
  - `Cancel`
  - `Save`

### Tab Order

Exact tab order:

1. `Caching`
2. `Work Planning`
3. `Projects`
4. `Billing`
5. `Integrations`
6. `Advanced`

### Caching Tab

Fields:

- `Projects Cache TTL (sec):`
- `Today Cache TTL (sec):`

Both are integer fields with minimum `1`.

### Work Planning Tab

Fields:

- `Vacation Days/Month:` integer field with minimum `0`
- `Project Targets (monthly hour goals):` grid of exactly 5 rows

Each target row has:

- project name text field
- hours numeric field

Serialization:

- rows with blank project name are dropped
- remaining rows serialize into `project_targets`

### Projects Tab

Purpose: canonical project billing definitions.

Header text:

- `Project Billing Definitions`
- `Fixed + required hours: carry over monthly  ·  Fixed + soft target: display only  ·  Fixed flat: freeform amount`

Grid shape:

- exactly 5 rows
- each row has a project popup and billing-type popup
- additional fields appear or hide based on billing type

Project popup:

- first option is always `—`
- remaining options come from cached Toggl project names
- if a saved project name is no longer in cache, it is inserted back into that row only

Billing type popup labels and their serialization:

| UI label | `billing_type` | `hour_tracking` |
|---|---|---|
| `Hourly` | `hourly` | `null` |
| `Hourly with cap` | `hourly_with_cap` | `null` |
| `Fixed + required hours` | `fixed_monthly` | `required` |
| `Fixed + soft target` | `fixed_monthly` | `soft` |
| `Fixed flat (no tracking)` | `fixed_monthly` | `none` |

Conditional fields:

- `Monthly $`: shown for all `fixed_monthly` rows
- `Target h`: shown for `fixed_monthly` with `required` or `soft`
- `Rate $/h`: shown for `hourly_with_cap`
- `Cap h`: shown for `hourly_with_cap`
- `Last billed`: shown for `hourly_with_cap`
- `Manual start of month carryover h (advanced)`: shown for:
  - `hourly_with_cap` without a non-blank `last_billed_date`
  - `fixed_monthly` with `hour_tracking = required`

Carryover behavior:

- The UI label always targets the previous month, for example `Mar carryover h`
- The field reads from `retainer_carryover.json`
- On save:
  - if billing type needs carryover and the field is non-blank, write that value as a manual carryover record for the previous month
  - for `hourly_with_cap` with `last_billed_date` present, force carryover to `0.0` for the previous month
  - if the carryover field is blank, do nothing and preserve any existing carryover file entry

Serialization:

- rows whose project popup is `—` are dropped
- `hourly` rows store only `{"billing_type": "hourly"}`
- `hourly_with_cap` rows store numeric fields and optional `last_billed_date`
- `fixed_monthly` rows always store `monthly_amount`, always store `hour_tracking`, and conditionally store `target_hours`

### Billing Tab

Header text:

- `Billing Reminders`

Help text explains:

- local notifications are sent while the app is running
- use 24-hour time
- notifications appear as `Python`
- Focus/Do Not Disturb caveats

Grid shape:

- exactly 4 rows

Each row has:

- enabled checkbox
- project popup
- task popup
- day/date popup
- time text field

Task popup:

- only one option exists: `Invoice`
- serializes to `task = "invoice"`

Day / Date popup options:

- weekdays: Monday through Sunday
- monthly options:
  - `Day 1 of month` through `Day 28 of month`
  - `Last day of month`
  - `2nd-to-last day`
  - `3rd-to-last day`

Default empty row UI state:

- enabled: off
- project: `—`
- task: `Invoice`
- day/date: `Friday`
- time: empty

Serialization:

- a row is dropped only if:
  - project is blank or `—`
  - time is blank
  - enabled is false
- any other partially-filled row is serialized and then validated

`Send Test Notification` button:

- immediately posts a sample macOS notification
- does not save settings

### Integrations Tab

Header text:

- `Integration Credentials`

Fields:

- `Toggl API Token:`
- `Toggl Workspace ID:`
- `Stripe API Key:`

Important quirk:

- These are plain text fields, not masked password controls.

Secondary section:

- header: `Project Billing Mapping`
- button: `Refresh Customers`
- help text explains Stripe customer mapping and Upwork contract ids

Grid shape:

- exactly 5 rows

Each row has:

- project popup
- Stripe customer popup
- Upwork contract id text field

Project popup source:

- same cached Toggl project list behavior as other project popups

Stripe customer popup source:

- loaded from live Stripe API using the entered or saved Stripe key
- failures are swallowed and result in an empty customer list
- first option is always `—`
- each loaded option label is `display_name_or_id (cus_xxx)`
- the represented value is the raw customer id

Serialization:

- only rows with a non-placeholder project and non-blank customer id are written to `stripe_project_customers`
- only rows with a non-placeholder project and non-blank contract id are written to `upwork_contracts`

`Refresh Customers` behavior:

- uses the current contents of the Stripe API key field, even if not yet saved
- preserves currently selected customer ids where possible

### Advanced Tab

Content:

- help text: `Advanced tools for troubleshooting and diagnostics.`
- button: `View API Audit Log`

Button behavior:

- runs AppleScript to open Terminal
- executes `tail -f ~/Library/Logs/toggl-api-audit.log`

## Save Flow

When the user clicks `Save`:

1. Read all widgets.
2. Build `project_targets`, `projects`, `stripe_project_customers`, `upwork_contracts`, and `billing_reminders`.
3. Build integration settings from the three credentials fields.
4. Start from `self.current_prefs.copy()`.
5. Overwrite only these keys:
   - `cache_ttl_projects`
   - `cache_ttl_today`
   - `vacation_days_per_month`
   - `project_targets`
   - `projects`
   - `stripe_project_customers`
   - `upwork_contracts`
   - `billing_reminders`
6. Leave all other existing preference keys untouched.
7. Validate `new_prefs`.
8. Apply integration validation.
9. If there are errors:
   - show modal alert titled `Invalid Preferences`
   - show only first 5 errors, then `... and N more errors` if needed
   - abort save
10. If valid:
   - write `preferences.json`
   - write `.env`
   - keep window open
   - temporarily change `Save` button label to `Saved ✓` for 1.5 seconds
   - call `NSApp.delegate().app.update_display()` if available

## Cancel Flow

- `Cancel` closes the window immediately.
- No settings are persisted.

## Reset Flow

When the user clicks `Reset to Defaults`:

1. Show confirmation modal:
   - title: `Reset to Defaults?`
   - body: `This will restore all settings to default values.`
2. If cancelled, do nothing.
3. If confirmed:
   - reset visible widgets to default/blank state
   - set in-memory `current_prefs` to defaults
   - do not persist anything yet
   - clear integration widgets to blank
   - clear project targets rows
   - clear project definition rows
   - clear Stripe/Upwork mapping rows
   - clear billing reminder rows

Important reset quirks:

1. Reset is not immediately saved. The user must still click `Save`.
2. Reset does not clear existing `.env` or `retainer_carryover.json` on disk until/unless a later `Save` overwrites relevant values.
3. Even after a reset-and-save, carryover records for removed projects are not explicitly deleted from `retainer_carryover.json`; they simply become unused.

## Settings Writes Outside The Preferences Window

A faithful reimplementation must include these non-window mutation paths.

### Dashboard Section Collapse State

The dashboard writes `preferences.json` directly when a user expands/collapses:

- `today`
- `week`
- `month`

Behavior:

- load current preferences
- deep-merge with default `dashboard_sections`
- update one section
- save immediately

This state is not editable in the Preferences window.

### Dashboard Upwork Contract Save

The dashboard can prompt for an Upwork contract id and then:

- validate that the id is digits only
- write it into `prefs['upwork_contracts'][project_name]`
- save `preferences.json` immediately

### Stripe Mapping Auto-Persist

After the dashboard successfully creates a Stripe draft invoice using a chosen customer:

- write `prefs['stripe_project_customers'][project_name] = customer_id`
- save `preferences.json` immediately

## 1:1 Reimplementation Checklist

To preserve current behavior exactly, the new stack must keep these implementation traits:

1. `preferences.json` is created lazily on first load, not at install time.
2. Preference loads shallow-merge over defaults, except `dashboard_sections`, which deep-merges.
3. Unknown top-level preference keys survive normal saves but are lost on reset-and-save.
4. Integration credentials are stored in repo-local `.env`, not in `preferences.json`.
5. Preferences UI uses fixed row counts:
   - 5 project target rows
   - 5 project definition rows
   - 5 Stripe/Upwork mapping rows
   - 4 billing reminder rows
6. Project dropdowns depend on cached Toggl project names, not a live fetch.
7. The “secure” credentials inputs are visibly plain text.
8. Invalid project numeric text becomes `0.0` before validation.
9. `project_targets` entered via the window persist as integers even though validation accepts floats.
10. Carryover is stored in a separate JSON file keyed by project and previous month.
11. Empty carryover inputs do not clear carryover records.
12. Reset does not persist until Save and does not actively delete old carryover entries.

## Suggested Port Boundaries

If the goal is strict parity, port these as separate modules/services:

1. `preferences` service
2. `integrations` service
3. `carryover` service
4. settings UI controller
5. dashboard-side settings mutators for collapse state, Stripe mapping, and Upwork contract save

That split matches the current implementation closely enough to preserve behavior without needing the original Python/AppKit structure.
