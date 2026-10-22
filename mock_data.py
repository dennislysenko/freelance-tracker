"""Mock data for testing the menu bar UI."""

def get_daily_earnings():
    """Return mock daily earnings data."""
    return {
        "total": 400.00,
        "hours": 5.25,
        "projects": [
            {
                "name": "Client A",
                "earnings": 225.00,
                "hours": 1.5,
                "rate": 150
            },
            {
                "name": "Client B",
                "earnings": 150.00,
                "hours": 1.25,
                "rate": 120
            },
            {
                "name": "Client C",
                "earnings": 25.00,
                "hours": 0.25,
                "rate": 100
            }
        ]
    }


def get_weekly_earnings():
    """Return mock weekly earnings data."""
    return {
        "total": 1200.00,
        "hours": 18.5
    }


def get_monthly_earnings():
    """Return mock monthly earnings data."""
    return {
        "total": 4800.00,
        "hours": 72.0
    }
