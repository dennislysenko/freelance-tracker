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
