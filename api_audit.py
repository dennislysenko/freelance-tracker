"""API request audit logging."""

import json
from datetime import datetime
from pathlib import Path

# Audit log location
LOG_DIR = Path.home() / "Library" / "Logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
AUDIT_LOG_PATH = LOG_DIR / "toggl-api-audit.log"


def log_api_request(endpoint, method="GET", status_code=None, error=None, cached=False, rate_limited=False):
    """
    Log an API request with timestamp and details.

    Args:
        endpoint: API endpoint (e.g., "/me/time_entries")
        method: HTTP method (GET, POST, etc.)
        status_code: HTTP status code
        error: Error message if request failed
        cached: Whether cached data was used
        rate_limited: Whether rate limit was hit
    """
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "endpoint": endpoint,
        "method": method,
        "status_code": status_code,
        "error": error,
        "cached": cached,
        "rate_limited": rate_limited
    }

    with open(AUDIT_LOG_PATH, 'a') as f:
        f.write(json.dumps(log_entry) + "\n")


def get_recent_audit_logs(limit=100):
    """Get recent audit log entries."""
    if not AUDIT_LOG_PATH.exists():
        return []

    logs = []
    with open(AUDIT_LOG_PATH, 'r') as f:
        lines = f.readlines()
        for line in lines[-limit:]:
            try:
                logs.append(json.loads(line.strip()))
            except json.JSONDecodeError:
                continue

    return logs


def is_currently_rate_limited():
    """
    Check if we're currently rate limited based on recent logs.
    Returns True if the most recent API call was rate limited.
    """
    logs = get_recent_audit_logs(limit=1)
    if logs and logs[0].get('rate_limited'):
        return True
    return False


def get_rate_limit_stats():
    """Get statistics about rate limiting."""
    logs = get_recent_audit_logs(limit=1000)

    total_requests = len(logs)
    rate_limited_requests = sum(1 for log in logs if log.get('rate_limited'))
    cached_requests = sum(1 for log in logs if log.get('cached'))

    return {
        'total': total_requests,
        'rate_limited': rate_limited_requests,
        'cached': cached_requests,
        'rate_limit_percentage': (rate_limited_requests / total_requests * 100) if total_requests > 0 else 0
    }
