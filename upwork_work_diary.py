"""Helpers for building Upwork work diary URLs."""

from datetime import date as date_type
from urllib.parse import urlencode


BASE_WORK_DIARY_URL = "https://www.upwork.com/nx/workdiary/"


def build_work_diary_url(contract_id, entry_date=None, tz="mine"):
    """Return the Upwork work diary URL for one contract and local date."""
    normalized_contract_id = str(contract_id or "").strip()
    if not normalized_contract_id or not normalized_contract_id.isdigit():
        raise ValueError("Upwork contract id must contain digits only.")

    if entry_date is None:
        normalized_date = date_type.today()
    elif isinstance(entry_date, date_type):
        normalized_date = entry_date
    elif isinstance(entry_date, str):
        normalized_date = date_type.fromisoformat(entry_date)
    else:
        raise TypeError("entry_date must be a date, ISO string, or None")

    query = urlencode(
        {
            "date": normalized_date.isoformat(),
            "contractId": normalized_contract_id,
            "tz": tz or "mine",
        }
    )
    return f"{BASE_WORK_DIARY_URL}?{query}"
