"""Tests for Upwork work diary URL helpers."""

from datetime import date

import pytest

from upwork_work_diary import build_work_diary_url


def test_build_work_diary_url_uses_expected_query_string():
    """The generated diary link should match Upwork's contract/date URL pattern."""
    url = build_work_diary_url("12345678", date(2026, 4, 14))

    assert (
        url ==
        "https://www.upwork.com/nx/workdiary/?date=2026-04-14&contractId=12345678&tz=mine"
    )


def test_build_work_diary_url_rejects_non_numeric_contract_ids():
    """Only digits are valid in stored Upwork contract ids."""
    with pytest.raises(ValueError):
        build_work_diary_url("worldchat-12345678", "2026-04-14")
