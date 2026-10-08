"""Tests for the MCP server's tool builders, with the data layer stubbed."""

from datetime import date
import json
import sys
from pathlib import Path

import pytest

import mcp_server as S
import mcp_support
import toggl_data


REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _restore_cache_only_flag(monkeypatch):
    # Tool calls force cache-only mode on the shared module; keep that from
    # leaking into other test files that exercise the real fetch path.
    monkeypatch.setattr(toggl_data, "CACHE_ONLY", toggl_data.CACHE_ONLY)


@pytest.fixture
def stubbed(monkeypatch, tmp_path):
    prefs = {
        "mcp_enabled": True,
        "mcp_anonymize": False,
        "cache_ttl_today": 1800,
        "vacation_days_per_month": 4,
        "days_off_keywords": ["pto"],
        "project_targets": {"Side Gig": 10},
        "retainer_hourly_rates": {},
        "monthly_rev_share": {"2026-04": 500},
        "projects": {
            "Acme Corp": {
                "billing_type": "hourly_with_cap",
                "hourly_rate": 150,
                "cap_hours": 40,
            },
            "Globex": {
                "billing_type": "fixed_monthly",
                "monthly_amount": 3000,
                "hour_tracking": "soft",
                "target_hours": 30,
            },
        },
    }
    monthly = {
        "total": 4500.0,
        "hours": 32.0,
        "fixed_earnings": 3000.0,
        "all_projects": [
            {"name": "Acme Corp", "hours": 10.0, "earnings": 1500.0, "rate": 150,
             "rate_source": "hourly_with_cap", "billable": True},
            {"name": "Globex", "hours": 22.0, "earnings": 3000.0, "rate": 100,
             "rate_source": "fixed_monthly", "billable": True},
        ],
        "projects": [],
    }
    projection = {
        "projected_earnings": 6000.0,
        "fixed_monthly_total": 3000.0,
        "rev_share_amount": 500.0,
        "projected_variable": 2500.0,
        "worked_days": 8,
        "total_business_days": 22,
        "workable_days": 18,
        "vacation_days": 4,
        "vacation_source": "flat",
        "daily_average": 187.5,
        "is_projection_capped": False,
        "capped_ceiling": None,
        "trace": {"current_total": 4500.0, "worked_day_dates": ["2026-04-01"]},
    }

    monkeypatch.setattr(S, "load_preferences", lambda: prefs)
    monkeypatch.setattr(S, "calculate_period_earnings",
                        lambda period: monthly if period == "monthly"
                        else {"total": 0.0, "hours": 0.0, "all_projects": []})
    monkeypatch.setattr(S, "calculate_monthly_projection", lambda: projection)
    monkeypatch.setattr(S, "get_previous_month_balance", lambda name: (0.0, "Mar"))
    monkeypatch.setattr(S, "_today", lambda: date(2026, 4, 15))

    entry_dir = tmp_path / "by_day"
    entry_dir.mkdir()
    monkeypatch.setattr(toggl_data, "ENTRY_CACHE_DIR", entry_dir)
    monkeypatch.setattr(S, "CACHE_DIR", tmp_path)
    for d in range(1, 16):
        (entry_dir / f"2026-04-{d:02d}.json").write_text('{"version":1,"entries":[]}')
    return prefs, entry_dir


def test_disabled_flag_short_circuits(stubbed):
    prefs, _ = stubbed
    prefs["mcp_enabled"] = False
    out = S.get_overview()
    assert out["error_type"] == "DisabledError"
    assert "Settings" in out["error"]
    # Freshness still answers so an agent can explain why.
    assert S.get_data_freshness()["mcp_enabled"] is False


def test_overview_shape(stubbed):
    out = S.get_overview()
    assert out["this_month"]["total"] == 4500.0
    assert out["projection"]["projected_earnings"] == 6000.0
    assert out["today_date"] == "2026-04-15"
    assert out["data_as_of"] is not None


def test_month_status_pacing_and_attention(stubbed):
    out = S.get_month_status()
    by_name = {p["name"]: p for p in out["projects"]}
    # Acme: 10/40 = 25% on day 15 of 30 (50%) -> ratio 0.5 -> "behind".
    acme = by_name["Acme Corp"]
    assert acme["billing_type"] == "hourly_with_cap"
    assert acme["pacing"]["band"] == "behind"
    assert acme["pacing"]["hours_needed"] == 30
    # Globex: 22/30 = 73% vs 50% -> ratio 1.47 -> "ahead".
    assert by_name["Globex"]["pacing"]["band"] == "ahead"
    # Side Gig has a target but no hours -> still listed, way behind.
    assert by_name["Side Gig"]["pacing"]["band"] == "way_behind"

    reasons = [(a["project"], a["band"]) for a in out["attention"]]
    assert reasons[0] == ("Side Gig", "way_behind")
    assert reasons[1] == ("Acme Corp", "behind")
    assert all("severity" not in a for a in out["attention"])
    assert out["attention"][1]["hours_per_remaining_day"] == pytest.approx(30 / 11, abs=0.01)


def test_project_rules_plain_english(stubbed):
    out = S.get_project_rules()
    text = "\n".join(out["rules_plain_english"])
    assert "Acme Corp: hourly at $150/h, capped at 40h per calendar month" in text
    assert "Globex: fixed $3000/month" in text and "30h soft target" in text
    assert "Side Gig: monthly hour target 10h" in text
    assert out["monthly_rev_share"] == {"2026-04": 500}


def test_anonymize_hides_names_and_scales_money(stubbed):
    prefs, _ = stubbed
    prefs["mcp_anonymize"] = True
    out = S.get_month_status()
    names = {p["name"] for p in out["projects"]}
    assert "Acme Corp" not in names
    assert any(n.startswith("Project ") for n in names)
    assert out["anonymized"] is True
    assert out["month_total"] != 4500.0
    rules = S.get_project_rules()
    assert "Acme" not in json.dumps(rules)


def test_time_entries_grouped_and_filtered(stubbed, monkeypatch):
    entries = [
        {"id": 1, "project_id": 1, "start": "2026-04-14T14:00:00Z", "duration": 3600, "description": "a"},
        {"id": 2, "project_id": 2, "start": "2026-04-14T16:00:00Z", "duration": 1800, "description": "b"},
        {"id": 3, "project_id": 1, "start": "2026-04-15T09:00:00Z", "duration": 7200, "description": "c"},
        {"id": 4, "project_id": 1, "start": "2026-04-15T10:00:00Z", "duration": -1, "description": "running"},
    ]
    monkeypatch.setattr(S, "get_entries_for_range", lambda s, e: entries)
    monkeypatch.setattr(S, "get_projects", lambda: {"1": {"name": "Acme Corp"}, "2": {"name": "Globex"}})

    out = S.get_time_entries("2026-04-14", "2026-04-15")
    assert out["total_hours"] == 3.5
    assert len(out["days"]) == 2

    only_acme = S.get_time_entries("2026-04-15", "2026-04-14", project="acme corp")
    assert only_acme["total_hours"] == 3.0
    assert all(e["project"] == "Acme Corp" for d in only_acme["days"] for e in d["entries"])


def test_time_entries_range_guard(stubbed):
    out = S.get_time_entries("2026-01-01", "2026-06-01")
    assert out["error_type"] == "ValueError"
    assert "92" in out["error"]
    assert S.get_time_entries("nope", "2026-01-01")["error_type"] == "ValueError"


def test_cache_miss_becomes_tool_error(stubbed, monkeypatch):
    def miss(s, e):
        raise toggl_data.CacheMissError([date(2026, 3, 1)])
    monkeypatch.setattr(S, "get_entries_for_range", miss)
    out = S.get_time_entries("2026-03-01", "2026-03-01")
    assert out["error_type"] == "CacheMissError"
    assert "Refresh Now" in out["error"]


def test_data_freshness_reports_stale_today(stubbed):
    out = S.get_data_freshness()
    assert out["today_shard_as_of"] is not None
    assert out["today_shard_stale"] is False
    assert out["this_month_as_of"] is not None


def test_registered_tools_and_prompt():
    tool_names = {t.name for t in S.mcp._tool_manager.list_tools()}
    assert tool_names == {
        "get_overview", "get_month_status", "get_projection",
        "get_project_rules", "get_time_entries", "get_data_freshness",
        "log_time", "update_entry", "delete_entry",
    }
    assert {p.name for p in S.mcp._prompt_manager.list_prompts()} == {"progress_checkin"}


def test_server_speaks_mcp_over_stdio(tmp_path):
    """End-to-end: spawn the real server and list tools through the SDK client.

    Preferences are read from the real user location; only the handshake and
    tool listing are exercised, which never touch Toggl.
    """
    import anyio
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    async def run():
        params = StdioServerParameters(
            command=sys.executable, args=[str(REPO / "mcp_server.py")], cwd=str(REPO),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                return sorted(t.name for t in tools.tools)

    names = anyio.run(run)
    assert "get_month_status" in names


# ---- mcp_support --------------------------------------------------------

def test_registration_snippets_point_at_repo():
    cmd = mcp_support.claude_code_command()
    assert cmd.startswith("claude mcp add --scope user --transport stdio freelance-tracker -- ")
    assert cmd.endswith("mcp_server.py")
    toml = mcp_support.codex_config_block()
    assert toml.startswith("[mcp_servers.freelance-tracker]\n")
    assert 'args = ["' in toml and "mcp_server.py" in toml


def test_detect_registrations(tmp_path):
    assert mcp_support.detect_registrations(tmp_path) == {"claude_code": False, "codex": False}

    (tmp_path / ".claude.json").write_text(json.dumps({
        "projects": {"/x": {"mcpServers": {"freelance-tracker": {}}}}
    }))
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text(
        "[mcp_servers.other]\ncommand='x'\n\n[mcp_servers.freelance-tracker]\ncommand = 'y'\n"
    )
    assert mcp_support.detect_registrations(tmp_path) == {"claude_code": True, "codex": True}

    (tmp_path / ".claude.json").write_text("{not json")
    assert mcp_support.detect_registrations(tmp_path)["claude_code"] is False
