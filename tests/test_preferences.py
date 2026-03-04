"""Unit tests for preferences validation."""

import pytest
from preferences import validate_preferences, DEFAULT_PREFERENCES


class TestPreferencesValidation:
    """Test the validate_preferences() function."""

    def test_valid_default_preferences(self):
        """Default preferences should pass validation."""
        errors = validate_preferences(DEFAULT_PREFERENCES.copy())
        assert errors == [], f"Default prefs failed: {errors}"

    def test_valid_custom_preferences(self):
        """Custom valid preferences should pass."""
        prefs = DEFAULT_PREFERENCES.copy()
        prefs['vacation_days_per_month'] = 8
        prefs['project_targets'] = {"TestProject": 40}
        prefs['retainer_hourly_rates'] = {"Retainer Client": 150}

        errors = validate_preferences(prefs)
        assert errors == []

    def test_missing_required_field(self):
        """Missing required fields should be caught."""
        prefs = DEFAULT_PREFERENCES.copy()
        del prefs['cache_ttl_projects']

        errors = validate_preferences(prefs)
        assert len(errors) == 1
        assert "Missing required field: 'cache_ttl_projects'" in errors[0]

    def test_wrong_type_int_to_str(self):
        """Type mismatches should be caught."""
        prefs = DEFAULT_PREFERENCES.copy()
        prefs['cache_ttl_projects'] = "86400"  # Should be int

        errors = validate_preferences(prefs)
        assert len(errors) == 1
        assert "'cache_ttl_projects'" in errors[0]
        assert "expected int, got str" in errors[0]

    def test_vacation_days_boundary_valid(self):
        """Vacation days boundaries (0 and 31) should be valid."""
        prefs = DEFAULT_PREFERENCES.copy()

        # Test 0
        prefs['vacation_days_per_month'] = 0
        errors = validate_preferences(prefs)
        assert errors == []

        # Test 31
        prefs['vacation_days_per_month'] = 31
        errors = validate_preferences(prefs)
        assert errors == []

    def test_vacation_days_out_of_range(self):
        """Vacation days outside 0-31 should be rejected."""
        prefs = DEFAULT_PREFERENCES.copy()

        # Test -1
        prefs['vacation_days_per_month'] = -1
        errors = validate_preferences(prefs)
        assert len(errors) == 1
        assert "'vacation_days_per_month'" in errors[0]
        assert "must be between 0 and 31" in errors[0]

        # Test 32
        prefs = DEFAULT_PREFERENCES.copy()
        prefs['vacation_days_per_month'] = 32
        errors = validate_preferences(prefs)
        assert len(errors) == 1
        assert "'vacation_days_per_month'" in errors[0]

    def test_project_targets_valid(self):
        """Valid project_targets should pass."""
        prefs = DEFAULT_PREFERENCES.copy()
        prefs['project_targets'] = {
            "Project A": 40,
            "Project B": 20,
            "Project C": 10.5,  # Float should be valid
        }

        errors = validate_preferences(prefs)
        assert errors == []

    def test_project_targets_wrong_type(self):
        """project_targets as non-dict should be rejected."""
        prefs = DEFAULT_PREFERENCES.copy()
        prefs['project_targets'] = ["ProjectA", 40]  # Should be dict

        errors = validate_preferences(prefs)
        assert len(errors) == 1
        assert "'project_targets'" in errors[0]
        assert "must be an object" in errors[0]

    def test_project_targets_negative_hours(self):
        """Negative project target hours should be rejected."""
        prefs = DEFAULT_PREFERENCES.copy()
        prefs['project_targets'] = {
            "ProjectA": -10
        }

        errors = validate_preferences(prefs)
        assert len(errors) == 1
        assert "project_targets.ProjectA" in errors[0]
        assert "must be non-negative" in errors[0]

    def test_project_targets_invalid_hours_type(self):
        """Non-numeric project target hours should be rejected."""
        prefs = DEFAULT_PREFERENCES.copy()
        prefs['project_targets'] = {
            "ProjectA": "40"  # Should be number
        }

        errors = validate_preferences(prefs)
        assert len(errors) == 1
        assert "project_targets.ProjectA" in errors[0]
        assert "must be a number" in errors[0]

    def test_multiple_errors(self):
        """Multiple validation errors should all be caught."""
        prefs = DEFAULT_PREFERENCES.copy()
        prefs['cache_ttl_projects'] = -100
        prefs['vacation_days_per_month'] = 50
        del prefs['cache_ttl_today']

        errors = validate_preferences(prefs)
        assert len(errors) == 3

    def test_empty_project_targets(self):
        """Empty project_targets dict should be valid."""
        prefs = DEFAULT_PREFERENCES.copy()
        prefs['project_targets'] = {}

        errors = validate_preferences(prefs)
        assert errors == []

    def test_retainer_hourly_rates_valid(self):
        """Valid retainer_hourly_rates should pass."""
        prefs = DEFAULT_PREFERENCES.copy()
        prefs['retainer_hourly_rates'] = {
            "Client A Retainer": 150,
            "Client B Retainer": 95.5,
        }

        errors = validate_preferences(prefs)
        assert errors == []

    def test_retainer_hourly_rates_wrong_type(self):
        """retainer_hourly_rates as non-dict should be rejected."""
        prefs = DEFAULT_PREFERENCES.copy()
        prefs['retainer_hourly_rates'] = ["Client A", 150]

        errors = validate_preferences(prefs)
        assert len(errors) == 1
        assert "'retainer_hourly_rates'" in errors[0]
        assert "must be an object" in errors[0]

    def test_retainer_hourly_rates_invalid_rate_type(self):
        """Non-numeric retainer rates should be rejected."""
        prefs = DEFAULT_PREFERENCES.copy()
        prefs['retainer_hourly_rates'] = {
            "Client A Retainer": "150"
        }

        errors = validate_preferences(prefs)
        assert len(errors) == 1
        assert "retainer_hourly_rates.Client A Retainer" in errors[0]
        assert "must be a number" in errors[0]

    def test_retainer_hourly_rates_non_positive(self):
        """Zero or negative retainer rates should be rejected."""
        prefs = DEFAULT_PREFERENCES.copy()
        prefs['retainer_hourly_rates'] = {
            "Client A Retainer": 0
        }

        errors = validate_preferences(prefs)
        assert len(errors) == 1
        assert "retainer_hourly_rates.Client A Retainer" in errors[0]
        assert "must be greater than 0" in errors[0]

    # --- projects key tests ---

    def test_projects_valid_hourly_with_cap(self):
        """Valid hourly_with_cap project should pass."""
        prefs = DEFAULT_PREFERENCES.copy()
        prefs['projects'] = {
            "Client A": {"billing_type": "hourly_with_cap", "hourly_rate": 150.0, "cap_hours": 20}
        }
        assert validate_preferences(prefs) == []

    def test_projects_valid_fixed_monthly_required(self):
        """Valid fixed_monthly/required project should pass."""
        prefs = DEFAULT_PREFERENCES.copy()
        prefs['projects'] = {
            "Hooli": {
                "billing_type": "fixed_monthly",
                "monthly_amount": 1500,
                "hour_tracking": "required",
                "target_hours": 10,
            }
        }
        assert validate_preferences(prefs) == []

    def test_projects_valid_fixed_monthly_soft(self):
        """Valid fixed_monthly/soft project should pass."""
        prefs = DEFAULT_PREFERENCES.copy()
        prefs['projects'] = {
            "Randonautica Retainer": {
                "billing_type": "fixed_monthly",
                "monthly_amount": 3000,
                "hour_tracking": "soft",
                "target_hours": 30,
            }
        }
        assert validate_preferences(prefs) == []

    def test_projects_valid_fixed_monthly_none(self):
        """Valid fixed_monthly/none project should pass (no target_hours needed)."""
        prefs = DEFAULT_PREFERENCES.copy()
        prefs['projects'] = {
            "Stark Industries": {
                "billing_type": "fixed_monthly",
                "monthly_amount": 4000,
                "hour_tracking": "none",
            }
        }
        assert validate_preferences(prefs) == []

    def test_projects_invalid_billing_type(self):
        """Unknown billing_type should be rejected."""
        prefs = DEFAULT_PREFERENCES.copy()
        prefs['projects'] = {"Client A": {"billing_type": "magic"}}
        errors = validate_preferences(prefs)
        assert len(errors) == 1
        assert "billing_type" in errors[0]

    def test_projects_hourly_with_cap_missing_cap_hours(self):
        """hourly_with_cap without cap_hours should be rejected."""
        prefs = DEFAULT_PREFERENCES.copy()
        prefs['projects'] = {
            "Client A": {"billing_type": "hourly_with_cap", "hourly_rate": 150.0}
        }
        errors = validate_preferences(prefs)
        assert any("cap_hours" in e for e in errors)

    def test_projects_fixed_monthly_required_missing_target_hours(self):
        """fixed_monthly/required without target_hours should be rejected."""
        prefs = DEFAULT_PREFERENCES.copy()
        prefs['projects'] = {
            "Client A": {
                "billing_type": "fixed_monthly",
                "monthly_amount": 2000,
                "hour_tracking": "required",
            }
        }
        errors = validate_preferences(prefs)
        assert any("target_hours" in e for e in errors)

    def test_projects_fixed_monthly_none_no_target_hours_ok(self):
        """fixed_monthly/none without target_hours is valid."""
        prefs = DEFAULT_PREFERENCES.copy()
        prefs['projects'] = {
            "Client A": {
                "billing_type": "fixed_monthly",
                "monthly_amount": 2000,
                "hour_tracking": "none",
            }
        }
        assert validate_preferences(prefs) == []

    def test_projects_not_dict_rejected(self):
        """projects as a list should be rejected."""
        prefs = DEFAULT_PREFERENCES.copy()
        prefs['projects'] = ["not", "a", "dict"]
        errors = validate_preferences(prefs)
        assert any("projects" in e for e in errors)

    def test_all_required_fields_present(self):
        """Verify all 3 required fields are checked."""
        required = [
            'cache_ttl_projects',
            'cache_ttl_today',
            'vacation_days_per_month',
        ]

        for field in required:
            prefs = DEFAULT_PREFERENCES.copy()
            del prefs[field]
            errors = validate_preferences(prefs)
            assert len(errors) >= 1, f"Should catch missing field: {field}"
            assert any(field in e for e in errors), f"Error should mention missing field: {field}"

    def test_negative_cache_ttl(self):
        """Negative cache TTL values should be rejected."""
        prefs = DEFAULT_PREFERENCES.copy()
        prefs['cache_ttl_projects'] = -1

        errors = validate_preferences(prefs)
        assert len(errors) == 1
        assert "'cache_ttl_projects'" in errors[0]
        assert "must be a positive integer" in errors[0]

        prefs = DEFAULT_PREFERENCES.copy()
        prefs['cache_ttl_today'] = 0

        errors = validate_preferences(prefs)
        assert len(errors) == 1
        assert "'cache_ttl_today'" in errors[0]
