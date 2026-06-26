"""Unit tests for the projection diagnostics export (diagnostics.py)."""

import json
import math

import pytest

import diagnostics as D


@pytest.fixture
def fake_data(monkeypatch):
    """Stub the data layer so the export is hermetic (no Toggl creds)."""
    prefs = {
        "vacation_days_per_month": 4,
        "projects": {
            "Acme Corp": {
                "billing_type": "fixed_monthly",
                "monthly_amount": 4000.0,
                "hour_tracking": "soft",
                "target_hours": 80,
            },
        },
        "retainer_hourly_rates": {"Beta LLC": 220.0},
        "project_targets": {"Acme Corp": 80},
        "monthly_rev_share": {"2026-06": 0},
        # Sensitive mappings that must never reach the export:
        "stripe_project_customers": {"Acme Corp": "cus_SECRET"},
        "upwork_contracts": {"Acme Corp": "CTR_SECRET"},
    }
    projection = {
        "projected_earnings": 7100.0, "fixed_monthly_total": 4000.0,
        "rev_share_amount": 0, "projected_variable": 3100.0,
        "worked_days": 8, "total_business_days": 21, "workable_days": 17,
        "vacation_days": 4, "daily_average": 387.5,
        "is_projection_capped": False, "capped_ceiling": None,
        "trace": {"current_total": 5200.0, "variable_earnings": 3200.0,
                  "has_uncapped_hourly": True,
                  "worked_day_dates": ["2026-06-01", "2026-06-02"]},
    }
    earnings = {
        "total": 5200.0, "fixed_earnings": 2000.0, "hours": 40.0,
        "projects": [
            {"name": "Acme Corp", "hours": 20.0, "billable": True,
             "earnings": 3000.0, "rate": 150.0, "rate_source": "hourly",
             "cap_fill_date": None, "time_blocks": ["should be dropped"]},
            {"name": "Beta LLC", "hours": 10.0, "billable": True,
             "earnings": 2200.0, "rate": 220.0, "rate_source": "toggl",
             "cap_fill_date": None},
        ],
        "all_projects": [
            {"name": "Acme Corp", "hours": 20.0, "billable": True},
            {"name": "Beta LLC", "hours": 10.0, "billable": True},
        ],
    }
    monkeypatch.setattr(D, "load_preferences", lambda: prefs)
    import toggl_data
    monkeypatch.setattr(toggl_data, "calculate_monthly_projection", lambda: projection)
    monkeypatch.setattr(toggl_data, "calculate_period_earnings", lambda period: earnings)
    return prefs, projection, earnings


def test_anonymized_export_has_no_secrets_or_real_names(fake_data):
    blob = json.dumps(D.build_diagnostics(anonymize=True))
    for bad in ["Acme", "Beta", "cus_SECRET", "CTR_SECRET",
                "150.0", "220.0", "stripe", "upwork"]:
        assert bad not in blob, f"leaked: {bad!r}"


def test_names_replaced_with_stable_labels(fake_data):
    blob = D.build_diagnostics(anonymize=True)
    assert set(blob["config"]["projects"]) == {"Project A"}
    assert set(blob["config"]["retainer_hourly_rates"]) == {"Project B"}


def test_rate_ratio_preserved_under_scaling(fake_data):
    blob = D.build_diagnostics(anonymize=True)
    rates = {p["name"]: p["rate"] for p in blob["month"]["earnings"]["projects"]}
    assert math.isclose(rates["Project B"] / rates["Project A"], 220.0 / 150.0, rel_tol=1e-3)


def test_projection_identity_survives_scaling(fake_data):
    pr = D.build_diagnostics(anonymize=True)["month"]["projection"]
    assert math.isclose(
        pr["projected_earnings"],
        pr["fixed_monthly_total"] + pr["rev_share_amount"] + pr["projected_variable"],
        rel_tol=1e-3,
    )


def test_hours_and_counts_are_not_scaled(fake_data):
    blob = D.build_diagnostics(anonymize=True)
    assert blob["month"]["earnings"]["hours"] == 40.0
    assert blob["month"]["projection"]["workable_days"] == 17
    assert blob["month"]["earnings"]["projects"][0]["hours"] == 20.0


def test_time_blocks_dropped_from_breakdown(fake_data):
    blob = D.build_diagnostics(anonymize=True)
    assert "time_blocks" not in blob["month"]["earnings"]["projects"][0]


def test_non_anonymized_keeps_real_values(fake_data):
    blob = D.build_diagnostics(anonymize=False)
    assert "Acme Corp" in blob["config"]["projects"]
    assert blob["month"]["earnings"]["projects"][0]["rate"] == 150.0
    assert "monetary_note" not in blob


def test_secret_mapping_keys_excluded_entirely(fake_data):
    blob = D.build_diagnostics(anonymize=False)
    assert "stripe_project_customers" not in blob["config"]
    assert "upwork_contracts" not in blob["config"]


def test_diagnostics_json_is_valid_json(fake_data):
    parsed = json.loads(D.diagnostics_json(anonymize=True))
    assert parsed["schema"] == D.SCHEMA
    assert parsed["anonymized"] is True
