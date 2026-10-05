"""
Natural-language time logging.

Turns an utterance like "put 1 hour of randonautica retainer at 9am for the past
2 days, no note" into a list of *proposed* Toggl time entries, which the caller
shows for confirmation before writing anything. Also classifies read questions
("how many hours on X this month?") into a local query the caller answers from
cache — those cost zero Toggl API calls.

Design notes:
  - Bring-your-own-key: the user supplies OPENAI_API_KEY in Settings >
    Integrations. Nothing here has a fallback key.
  - Project names are passed to the model as a JSON-schema *enum*, so it cannot
    invent a project that does not exist in the workspace.
  - The model never does timezone math. It returns a local calendar date and a
    local wall-clock time; `resolve_entries` converts those to the aware
    datetimes `toggl_data.create_time_entry` requires.
  - OpenAI calls are deliberately NOT written to the Toggl audit log; that log
    backs the documented Toggl call counts and must stay Toggl-only.
"""

import json
import os
from datetime import date as _date, datetime, time as _time, timedelta

import requests

from integrations import load_integration_settings

# The Responses API specifically, not chat/completions: a restricted project key
# with "Model capabilities: Request" grants Write on /v1/responses and only
# Request on the other endpoints, so this is the one that works with the
# tightly-scoped key the setup guide tells users to mint.
API_URL = "https://api.openai.com/v1/responses"

# Pinned so a user's key works on first run. Override with OPENAI_MODEL in .env
# if you want a different tier; any model supporting strict structured outputs
# will do.
DEFAULT_MODEL = "gpt-4.1-mini"

REQUEST_TIMEOUT = 30


# --- Error taxonomy -------------------------------------------------------
# These map 1:1 onto distinct user actions, so the UI can say something useful
# instead of "AI request failed".


class NLError(Exception):
    """Base class for natural-language logging failures."""


class NLConfigError(NLError):
    """No API key, or nothing to work with (e.g. no projects)."""


class NLAuthError(NLError):
    """Key was rejected — user must re-enter it."""


class NLQuotaError(NLError):
    """Key is valid but the account has no billing credit."""


class NLRateLimitError(NLError):
    """Temporary rate limit — retrying later will work."""


class NLServiceError(NLError):
    """Network failure or an OpenAI-side error."""


def get_api_key():
    """Return the user's OpenAI key, or '' if they have not set one."""
    return (
        load_integration_settings().get("OPENAI_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or ""
    ).strip()


def is_configured():
    """True when natural-language logging is available to this user."""
    return bool(get_api_key())


def _model():
    return (os.getenv("OPENAI_MODEL") or "").strip() or DEFAULT_MODEL


def _post(api_key, payload):
    """POST to OpenAI, translating every failure into the taxonomy above."""
    try:
        response = requests.post(
            API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.exceptions.RequestException as exc:
        raise NLServiceError(f"Could not reach OpenAI: {exc}") from exc

    if response.status_code == 401:
        raise NLAuthError(
            "OpenAI rejected the API key. Check it in Settings > Integrations."
        )

    if response.status_code == 429:
        # A fresh key on an account that never added billing returns 429 with
        # code "insufficient_quota" — a completely different fix from a real
        # rate limit, so it gets its own error.
        code = ""
        try:
            code = (response.json().get("error") or {}).get("code") or ""
        except ValueError:
            pass
        if code == "insufficient_quota":
            raise NLQuotaError(
                "This OpenAI key has no available credit. Add a payment method "
                "and a small budget at platform.openai.com > Settings > Billing."
            )
        raise NLRateLimitError("OpenAI is rate limiting this key. Try again shortly.")

    if response.status_code >= 400:
        detail = ""
        try:
            detail = (response.json().get("error") or {}).get("message") or ""
        except ValueError:
            detail = (response.text or "")[:200]
        raise NLServiceError(f"OpenAI error {response.status_code}: {detail}")

    try:
        return response.json()
    except ValueError as exc:
        raise NLServiceError("OpenAI returned a malformed response.") from exc


def validate_api_key(api_key):
    """
    Cheap round-trip used by the settings Save flow, so a bad key is discovered
    in the settings pane rather than halfway through logging time.

    Returns (ok: bool, message: str).
    """
    api_key = (api_key or "").strip()
    if not api_key:
        return False, "No key provided."
    try:
        _post(
            api_key,
            {
                "model": _model(),
                "input": "ok",
                # Responses enforces a floor of 16 on max_output_tokens.
                "max_output_tokens": 16,
            },
        )
    except NLError as exc:
        return False, str(exc)
    return True, "Key verified."


# --- Prompting ------------------------------------------------------------


def _schema(project_names):
    project_enum = list(project_names)
    # Responses flattens the structured-output format: type/name/strict/schema
    # sit together, rather than nesting under a "json_schema" key the way
    # chat/completions does.
    return {
        "type": "json_schema",
        "name": "time_command",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["intent", "entries", "query", "message"],
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": ["log", "question", "unclear"],
                    "description": (
                        "'log' to create time entries, 'question' to read "
                        "existing data, 'unclear' if you cannot tell."
                    ),
                },
                "entries": {
                    "type": "array",
                    "description": "One object per time entry to create. Empty unless intent is 'log'.",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "project",
                            "date_kind",
                            "date_absolute",
                            "date_days_ago",
                            "date_weekday",
                            "date_direction",
                            "start_time",
                            "duration_minutes",
                            "description",
                            "billable",
                        ],
                        "properties": {
                            "project": {
                                "type": "string",
                                "enum": project_enum,
                                "description": "Must be one of the workspace projects.",
                            },
                            "date_kind": {
                                "type": "string",
                                "enum": ["absolute", "days_ago", "weekday"],
                                "description": (
                                    "How the user referred to the day. Use "
                                    "'days_ago' for today/yesterday/'the past N "
                                    "days', 'weekday' when they named a day of the "
                                    "week, 'absolute' only for an explicit calendar "
                                    "date. Do NOT convert between these yourself."
                                ),
                            },
                            "date_absolute": {
                                "type": ["string", "null"],
                                "description": "YYYY-MM-DD. Only when date_kind is 'absolute'.",
                            },
                            "date_days_ago": {
                                "type": ["integer", "null"],
                                "description": (
                                    "Only when date_kind is 'days_ago'. 0 = today, "
                                    "1 = yesterday, 2 = the day before that."
                                ),
                            },
                            "date_weekday": {
                                "type": ["string", "null"],
                                "enum": [
                                    "Monday",
                                    "Tuesday",
                                    "Wednesday",
                                    "Thursday",
                                    "Friday",
                                    "Saturday",
                                    "Sunday",
                                    None,
                                ],
                                "description": (
                                    "Only when date_kind is 'weekday'. The day the "
                                    "user named, verbatim."
                                ),
                            },
                            "date_direction": {
                                "type": ["string", "null"],
                                "enum": ["past", "future", None],
                                "description": (
                                    "Only when date_kind is 'weekday'. 'past' for a "
                                    "bare weekday or 'last X'; 'future' only for an "
                                    "explicit 'next X'."
                                ),
                            },
                            "start_time": {
                                "type": "string",
                                "description": "Local wall-clock start, 24-hour HH:MM.",
                            },
                            "duration_minutes": {"type": "integer"},
                            "description": {
                                "type": ["string", "null"],
                                "description": "Entry note. Null when the user says no note.",
                            },
                            "billable": {"type": "boolean"},
                        },
                    },
                },
                "query": {
                    "type": ["object", "null"],
                    "additionalProperties": False,
                    "required": ["project", "start_date", "end_date"],
                    "description": "Local data to look up. Null unless intent is 'question'.",
                    "properties": {
                        "project": {
                            "type": ["string", "null"],
                            "enum": project_enum + [None],
                            "description": "Null means all projects.",
                        },
                        "start_date": {"type": "string", "description": "YYYY-MM-DD, inclusive."},
                        "end_date": {"type": "string", "description": "YYYY-MM-DD, inclusive."},
                    },
                },
                "message": {
                    "type": ["string", "null"],
                    "description": (
                        "Short note to the user: what you assumed, or what is "
                        "missing when intent is 'unclear'."
                    ),
                },
            },
        },
    }


SYSTEM_PROMPT = """\
You convert a freelancer's shorthand into structured time-tracking commands.

Rules:
- DO NOT do calendar arithmetic. Describe the day the way the user did, using
  date_kind, and the app resolves it to a real date:
    "yesterday"          -> date_kind "days_ago", date_days_ago 1
    "the past 2 days"    -> two entries, date_days_ago 1 and 2
    "friday afternoon"   -> date_kind "weekday", date_weekday "Friday",
                            date_direction "past"
    "next Friday"        -> same, date_direction "future"
    "on 2026-07-31"      -> date_kind "absolute", date_absolute "2026-07-31"
  Never compute which calendar date a weekday falls on — that is the app's job.
- People log time they have ALREADY worked, so a bare weekday means the most
  recent one that has passed. Use date_direction "future" only when the user is
  explicit, and say so in `message` when you do.
- Return local wall-clock times. Never apply a timezone offset yourself.
- When the user gives only a vague time of day, use these defaults so the same
  command always produces the same entry: morning 09:00, afternoon 13:00,
  evening 18:00. If they give no time at all, use 09:00.
- Match the project by meaning, not exact string ("randonautica retainer" may
  be "Randonautica - Retainer"). Choose only from the provided list.
- "no note" / "no description" means description must be null.
- Default billable to true unless the user says otherwise.
- One object per day: "1 hour at 9am for the past 2 days" is TWO entries.
- If the project, duration, or date is genuinely ambiguous, use intent
  "unclear" and say what you need in `message`. Do not guess a project.
- The conversation so far may be included. Earlier assistant turns list the
  entries that were proposed, with absolute dates. If the new message refines
  or corrects an earlier proposal ("round to the half hour", "make it 2 hours",
  "actually that was yesterday", "same again for Globex"), return intent "log"
  with the COMPLETE corrected entries: every field restated, date_kind
  "absolute" with the date from the earlier proposal unless the user changed
  it. Never return "unclear" for a message that clearly modifies the previous
  proposal.
"""


def _calendar_reference(now, back=14, forward=7):
    """
    Spell out recent and upcoming dates with their weekday names.

    Models are unreliable at weekday arithmetic — asked for "friday afternoon"
    they will confidently return a Saturday. Handing them an explicit calendar
    turns the date lookup into reading rather than computing.
    """
    today = now.date()
    lines = []
    for offset in range(-back, forward + 1):
        day = today + timedelta(days=offset)
        if offset == 0:
            marker = "  <- TODAY"
        elif offset < 0:
            marker = f"  ({-offset} day{'s' if offset != -1 else ''} ago)"
        else:
            marker = "  (future)"
        lines.append(f"  {day.isoformat()} {day.strftime('%A')}{marker}")
    return "Date reference:\n" + "\n".join(lines)


def interpret(utterance, project_names, now=None, api_key=None, history=None):
    """
    Classify an utterance and return the raw structured result.

    Args:
        utterance: what the user typed.
        project_names: list of Toggl project names the user can log against.
        now: local aware/naive datetime treated as "now" (defaults to now).
        api_key: override; defaults to the configured key.
        history: prior conversation as [{"role": "user"|"assistant",
            "content": str}, ...], oldest first. Lets a follow-up like "round
            to the half hour" refine the previous proposal instead of reading
            as a command with no subject.

    Returns the parsed dict: {intent, entries, query, message}.
    """
    api_key = (api_key or get_api_key()).strip()
    if not api_key:
        raise NLConfigError(
            "OpenAI API key is not configured. Add it in Settings > Integrations."
        )
    if not project_names:
        raise NLConfigError("No Toggl projects available to log against.")

    now = now or datetime.now()
    context = (
        f"Current local date and time: {now.strftime('%Y-%m-%d %H:%M')} "
        f"({now.strftime('%A')}).\n"
        f"{_calendar_reference(now)}\n"
        f"Available projects: {json.dumps(list(project_names))}"
    )

    data = _post(
        api_key,
        {
            "model": _model(),
            "input": [
                {"role": "system", "content": SYSTEM_PROMPT},
                *[
                    {"role": m["role"], "content": m["content"]}
                    for m in (history or [])
                    if m.get("role") in ("user", "assistant") and m.get("content")
                ],
                {"role": "user", "content": f"{context}\n\nCommand: {utterance}"},
            ],
            "text": {"format": _schema(project_names)},
        },
    )

    text = _output_text(data)
    if not text:
        raise NLServiceError("OpenAI returned an empty reply.")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise NLServiceError("Could not read OpenAI's structured reply.") from exc


def _output_text(data):
    """
    Pull the assistant text out of a Responses payload.

    Responses returns a list of output items (reasoning, messages, tool calls),
    so the text is not at a fixed index the way chat/completions' choices[0]
    was — walk the items and collect the output_text parts.
    """
    parts = []
    for item in (data or {}).get("output") or []:
        if item.get("type") != "message":
            continue
        for chunk in item.get("content") or []:
            if chunk.get("type") == "output_text" and chunk.get("text"):
                parts.append(chunk["text"])
    return "".join(parts)


# --- Turning a parse into something Toggl can accept ----------------------


def project_name_map(projects):
    """
    Build {project name -> project id} from `toggl_data.get_projects()`, which
    is keyed the other way ({id: {"name": ...}}).
    """
    mapping = {}
    for project_id, info in (projects or {}).items():
        name = (info or {}).get("name")
        if name:
            mapping[name] = int(project_id)
    return mapping


WEEKDAYS = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


def resolve_day(item, today=None):
    """
    Turn the model's description of a day into an actual date, in Python.

    The model is asked to say *how the user referred to* the day, never to
    compute a calendar date. Left to itself it will claim "the most recent
    Friday is 2026-08-01" — a Saturday — and then correctly label that date
    Saturday, so no amount of self-cross-checking catches it. Doing the
    arithmetic here removes the failure mode instead of detecting it.
    """
    today = today or datetime.now().date()
    kind = item.get("date_kind")

    if kind == "absolute":
        raw = item.get("date_absolute")
        if not raw:
            raise ValueError(f"Entry says absolute date but gives none: {item}")
        return datetime.strptime(raw, "%Y-%m-%d").date()

    if kind == "days_ago":
        offset = item.get("date_days_ago")
        if offset is None:
            raise ValueError(f"Entry says days_ago but gives no offset: {item}")
        offset = int(offset)
        if offset < 0:
            raise ValueError(f"days_ago must not be negative: {item}")
        return today - timedelta(days=offset)

    if kind == "weekday":
        name = item.get("date_weekday")
        if name not in WEEKDAYS:
            raise ValueError(f"Entry says weekday but names none: {item}")
        target = WEEKDAYS.index(name)
        if item.get("date_direction") == "future":
            # "next Friday" said on a Friday means the one coming, not today.
            step = (target - today.weekday()) % 7 or 7
            return today + timedelta(days=step)
        # A bare or past weekday said on that same weekday means today.
        return today - timedelta(days=(today.weekday() - target) % 7)

    raise ValueError(f"Unrecognized date_kind in proposed entry: {item}")


def resolve_entries(parsed_entries, projects_map):
    """
    Convert parsed entries into kwargs for `toggl_data.create_time_entry`.

    `projects_map` maps project name -> project id.

    Returns a list of dicts with an aware `start`, plus `label`/`day` for the
    confirmation UI. Raises ValueError on anything unparseable, so a malformed
    proposal never reaches the write path.
    """
    resolved = []
    for item in parsed_entries or []:
        name = item.get("project")
        if name not in projects_map:
            raise ValueError(f"Unknown project: {name!r}")

        day = resolve_day(item)
        try:
            hour, minute = (int(part) for part in item["start_time"].split(":"))
            start_local = datetime.combine(day, _time(hour, minute))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Bad start time in proposed entry: {item}") from exc

        minutes = int(item.get("duration_minutes") or 0)
        if minutes <= 0:
            raise ValueError(f"Proposed entry has no duration: {item}")

        # astimezone() with no argument attaches the machine's local zone,
        # which is what create_time_entry needs (it rejects naive datetimes).
        start = start_local.astimezone()

        resolved.append(
            {
                "start": start,
                "duration_seconds": minutes * 60,
                "description": item.get("description") or "",
                "project_id": projects_map[name],
                "billable": bool(item.get("billable", True)),
                "project_name": name,
                "day": day,
                "label": (
                    f"{start_local.strftime('%a %b %-d, %-I:%M%p').lower()}"
                    f" · {minutes / 60:.2f}h · {name}"
                ),
            }
        )
    return resolved


def find_collisions(resolved, existing_entries):
    """
    Flag proposals that overlap an entry already in Toggl.

    "Log the past 2 days" run twice would otherwise silently double-log. Returns
    a list of indices into `resolved` that overlap something existing, so the
    confirmation card can warn before the user applies.

    `existing_entries` are Toggl entry dicts (as cached by toggl_data), using
    their `start` and `duration` fields.
    """
    spans = []
    for entry in existing_entries or []:
        raw_start = entry.get("start")
        duration = entry.get("duration")
        if not raw_start or not isinstance(duration, int) or duration <= 0:
            continue  # running entries have a negative duration; skip them
        try:
            begin = datetime.fromisoformat(str(raw_start).replace("Z", "+00:00"))
        except ValueError:
            continue
        spans.append((begin, begin + timedelta(seconds=duration)))

    collisions = []
    for index, item in enumerate(resolved):
        begin = item["start"]
        end = begin + timedelta(seconds=item["duration_seconds"])
        if any(begin < other_end and other_begin < end for other_begin, other_end in spans):
            collisions.append(index)
    return collisions


if __name__ == "__main__":
    # Harness for checking parse quality without the dashboard:
    #   python nl_time.py "1 hour of randonautica retainer at 9am the past 2 days, no note"
    import sys

    import toggl_data

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    projects_map = project_name_map(toggl_data.get_projects())
    try:
        result = interpret(" ".join(sys.argv[1:]), sorted(projects_map))
    except NLError as error:
        print(f"{type(error).__name__}: {error}")
        sys.exit(1)

    print(json.dumps(result, indent=2))

    if result.get("intent") == "log":
        try:
            for item in resolve_entries(result.get("entries"), projects_map):
                print(f"  would log: {item['label']}")
        except ValueError as error:
            print(f"  unusable proposal: {error}")
