# Toggl API Integration Reference

## Quick Reference

### Base URL
```
https://api.track.toggl.com/api/v9
```

### Authentication
```bash
# Basic auth: API_TOKEN as username, "api_token" as password
curl -u YOUR_API_TOKEN:api_token https://api.track.toggl.com/api/v9/me
```

## Key Endpoints

### 1. Get Current User Info
```
GET /me
```
Returns user details including default workspace.

### 2. Get Time Entries (Main Endpoint)
```
GET /me/time_entries
Parameters:
  - start_date (ISO 8601): Filter start date
  - end_date (ISO 8601): Filter end date
```

**Example:**
```bash
curl -u API_TOKEN:api_token \
  "https://api.track.toggl.com/api/v9/me/time_entries?start_date=2025-12-01T00:00:00Z&end_date=2025-12-01T23:59:59Z"
```

**Response:**
```json
[
  {
    "id": 4199631084,
    "workspace_id": 3962700,
    "project_id": 210613737,
    "start": "2025-12-01T20:15:00+00:00",
    "stop": "2025-12-01T20:45:00+00:00",
    "duration": 1800,
    "description": "email sequence",
    "billable": false
  }
]
```

### 3. Get Projects
```
GET /me/projects
```

**Response includes:**
- `id`: Project ID
- `name`: Project name
- `rate`: Hourly rate (null if not set)
- `billable`: Boolean
- `client_name`: Associated client

### 4. Get Current Running Entry
```
GET /me/time_entries/current
```

Returns the currently running time entry, or `null` if none.

### 5. Create Time Entry
```
POST /workspaces/{workspace_id}/time_entries
Content-Type: application/json

{
  "workspace_id": 3962700,
  "description": "Working on feature X",
  "start": "2025-12-01T10:00:00Z",
  "duration": 3600,  // seconds, or -1 for running
  "project_id": 210613737,
  "billable": true
}
```

### 6. Update Time Entry
```
PUT /workspaces/{workspace_id}/time_entries/{entry_id}
Content-Type: application/json

{
  "description": "Updated description",
  "duration": 7200
}
```

### 7. Stop Running Entry
```
PATCH /workspaces/{workspace_id}/time_entries/{entry_id}/stop
```

### 8. Delete Time Entry
```
DELETE /workspaces/{workspace_id}/time_entries/{entry_id}
```

## Rate Limits

- **30 requests per hour** per API token
- Rate limit resets on the hour
- Headers returned:
  - `X-RateLimit-Limit`: 30
  - `X-RateLimit-Remaining`: remaining requests
  - `X-RateLimit-Reset`: Unix timestamp when limit resets

## Best Practices for Menu Bar App

### 1. Minimize API Calls

**Smart caching strategy:**
```python
# Only fetch today's data (1 API call)
GET /me/time_entries?start_date=today&end_date=today

# Cache projects for 24 hours (1 API call per day)
GET /me/projects

# For weekly/monthly: cache historical data, only fetch today fresh
```

### 2. Recommended Refresh Intervals

```python
# Today's earnings: every 30 minutes
# Projects list: every 24 hours
# Running entry: every 5 minutes (if tracking)
```

### 3. Error Handling

```python
import requests

try:
    response = requests.get(url, auth=(token, "api_token"), timeout=10)
    response.raise_for_status()
except requests.exceptions.Timeout:
    # Show cached data, notify user of connection issue
    pass
except requests.exceptions.HTTPError as e:
    if e.response.status_code == 429:
        # Rate limit hit - use cached data
        pass
    elif e.response.status_code == 401:
        # Invalid API token - prompt user to re-enter
        pass
```

## Data Structure Reference

### Time Entry Object
```python
{
    "id": int,
    "workspace_id": int,
    "project_id": int or null,
    "task_id": int or null,
    "billable": bool,
    "start": "ISO 8601 datetime",
    "stop": "ISO 8601 datetime" or null,
    "duration": int (seconds, negative if running),
    "description": str or null,
    "tags": [str],
    "tag_ids": [int],
    "at": "ISO 8601 datetime" (last modified)
}
```

### Project Object
```python
{
    "id": int,
    "workspace_id": int,
    "client_id": int or null,
    "name": str,
    "rate": float or null,  # Hourly rate
    "billable": bool,
    "client_name": str or null,
    "active": bool,
    "currency": str or null  # e.g., "USD"
}
```

## Calculating Earnings

```python
def calculate_earnings(time_entries, projects):
    """Calculate earnings from time entries and project rates."""
    earnings = 0

    for entry in time_entries:
        # Skip if no project or not billable
        if not entry.get("project_id") or not entry.get("billable"):
            continue

        project = projects.get(str(entry["project_id"]))
        if not project or not project.get("rate"):
            continue

        # Convert duration (seconds) to hours
        hours = entry["duration"] / 3600

        # Calculate earnings
        earnings += hours * project["rate"]

    return earnings
```

## Webhooks (Future Enhancement)

Toggl doesn't currently support webhooks, so polling is required. However, you can optimize by:

1. Using the `at` field to detect changes
2. Only fetching entries modified since last check
3. Implementing smart refresh based on user activity

## Testing API Calls

### Quick test in Python:
```python
import requests
import os
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("TOGGL_API_TOKEN")

# Test connection
response = requests.get(
    "https://api.track.toggl.com/api/v9/me",
    auth=(token, "api_token")
)
print(response.json())
```

### Check rate limit:
```python
response = requests.get(
    "https://api.track.toggl.com/api/v9/me/time_entries",
    auth=(token, "api_token")
)
print(f"Remaining: {response.headers.get('X-RateLimit-Remaining')}")
print(f"Resets at: {response.headers.get('X-RateLimit-Reset')}")
```

## Useful Time Calculations

### Get date ranges:
```python
from datetime import datetime, timedelta, timezone

# Today
today = datetime.now(timezone.utc).date()
today_start = datetime.combine(today, datetime.min.time()).replace(tzinfo=timezone.utc)
today_end = datetime.combine(today, datetime.max.time()).replace(tzinfo=timezone.utc)

# This week (Monday to Sunday)
start_of_week = today - timedelta(days=today.weekday())
end_of_week = start_of_week + timedelta(days=6)

# This month
import calendar
start_of_month = today.replace(day=1)
last_day = calendar.monthrange(today.year, today.month)[1]
end_of_month = today.replace(day=last_day)
```

## Resources

- [Official API Docs](https://engineering.toggl.com/docs/)
- [Time Entries Endpoint](https://engineering.toggl.com/docs/api/time_entries/)
- [Projects Endpoint](https://engineering.toggl.com/docs/projects/)
- [API Rate Limits](https://engineering.toggl.com/docs/api/#rate-limiting)
