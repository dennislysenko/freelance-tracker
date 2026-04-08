# Menu Bar App Architecture

> ⚠️ **HISTORICAL — DO NOT TREAT AS CURRENT.** This document is the original pre-implementation design from before the project was built. It describes a hypothetical file layout (`app.py`, `menu_builder.py`, `earnings_calculator.py`, etc.) that does not exist, and a rumps dropdown as the primary interface — the actual app uses a WebKit dashboard popover (`dashboard_panel.py`) as the canonical UI. For the current architecture and feature set, see `docs/SOT.md`, `README.md`, and `AGENTS.md`. Kept here only for historical reference.

## High-Level Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Menu Bar Icon                     │
│              (Shows: "💰 $400")                     │
└────────────────────┬────────────────────────────────┘
                     │
                     ↓ (user clicks)
┌─────────────────────────────────────────────────────┐
│                  Dropdown Menu                      │
│  ┌───────────────────────────────────────────────┐  │
│  │ TODAY: $400 (5.25h)                           │  │
│  │ - Acme Inc: $225                         │  │
│  │ - Initech: $150                            │  │
│  │                                               │  │
│  │ THIS WEEK: $400                               │  │
│  │ THIS MONTH: $400                              │  │
│  │                                               │  │
│  │ [Refresh] [Settings] [Quit]                   │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
                     ↑
                     │
        ┌────────────┴───────────┐
        │                        │
   ┌────▼─────┐          ┌──────▼──────┐
   │  Cache   │          │  Toggl API  │
   │  Layer   │◄─────────┤   Client    │
   └──────────┘          └─────────────┘
        │                        │
        ↓                        ↓
   ~/.toggl_cache/      api.track.toggl.com
```

## Component Breakdown

### 1. Presentation Layer (UI)

**Responsibilities:**
- Display earnings in menu bar
- Render dropdown menu
- Handle user interactions
- Update UI on data changes

**Files:**
- `app.py` - Main rumps application
- `menu_builder.py` - Dynamic menu construction
- `formatter.py` - Format data for display

### 2. Business Logic Layer

**Responsibilities:**
- Calculate earnings from raw data
- Aggregate data by period (daily/weekly/monthly)
- Apply business rules (billable vs non-billable)

**Files:**
- `earnings_calculator.py` - Core calculation logic
- `data_aggregator.py` - Aggregate entries by project/date

### 3. Data Access Layer

**Responsibilities:**
- Fetch data from Toggl API
- Manage cache
- Handle API rate limits
- Error handling and retries

**Files:**
- `toggl_api.py` - API client
- `cache_manager.py` - Cache read/write
- `rate_limiter.py` - Track API usage

### 4. Configuration Layer

**Responsibilities:**
- Load API credentials
- Store user preferences
- Manage settings

**Files:**
- `config.py` - Configuration management
- `.env` - Environment variables
- `settings.json` - User preferences

## Data Flow Diagram

```
User clicks menu bar icon
         ↓
app.py receives click event
         ↓
Call earnings_calculator.get_all_earnings()
         ↓
Check cache_manager.get_cached_data()
         ↓
    Is cached data fresh?
         ├─ YES → Return cached data
         │         ↓
         │    Format and display
         │
         └─ NO → Call toggl_api.fetch_entries()
                  ↓
            Check rate_limiter.can_make_request()
                  ├─ YES → Make API call
                  │         ↓
                  │    Cache response
                  │         ↓
                  │    Calculate earnings
                  │         ↓
                  │    Format and display
                  │
                  └─ NO → Use stale cache
                           ↓
                      Show warning to user
```

## File Structure

```
toggl-menubar/
│
├── app.py                      # Main entry point
├── config.py                   # Configuration loader
├── requirements.txt            # Dependencies
├── setup.py                    # py2app build script
├── .env                        # API credentials (gitignored)
├── .env.example                # Template
│
├── src/
│   ├── __init__.py
│   │
│   ├── ui/
│   │   ├── __init__.py
│   │   ├── menu_builder.py    # Build dynamic menus
│   │   └── formatter.py       # Format data for display
│   │
│   ├── business/
│   │   ├── __init__.py
│   │   ├── earnings_calculator.py
│   │   └── data_aggregator.py
│   │
│   ├── data/
│   │   ├── __init__.py
│   │   ├── toggl_api.py       # API client
│   │   ├── cache_manager.py   # Cache operations
│   │   └── rate_limiter.py    # Rate limit tracking
│   │
│   └── utils/
│       ├── __init__.py
│       ├── date_helpers.py    # Date range calculations
│       └── logger.py          # Logging setup
│
├── assets/
│   ├── icon.png               # 16x16 menu bar icon
│   ├── icon@2x.png            # 32x32 retina icon
│   └── icon.icns              # macOS icon file
│
├── tests/
│   ├── __init__.py
│   ├── test_api.py
│   ├── test_cache.py
│   └── test_calculator.py
│
└── docs/
    ├── menubar-app-plan.md
    ├── api-integration.md
    └── architecture.md
```

## Key Design Decisions

### 1. Caching Strategy

**Problem:** API has 30 requests/hour limit

**Solution:**
```python
# Cache structure
cache = {
    "projects": {
        "data": [...],
        "timestamp": 1701436800,
        "ttl": 86400  # 24 hours
    },
    "today_entries": {
        "data": [...],
        "timestamp": 1701436800,
        "ttl": 1800  # 30 minutes
    },
    "historical_entries": {
        "2025-11-30": [...],  # Immutable - never refetch
        "2025-11-29": [...],
    }
}
```

**Rules:**
- Historical data (before today) is immutable
- Today's data refreshes every 30 minutes
- Projects refresh every 24 hours
- Always serve from cache if available, fetch in background

### 2. State Management

**Problem:** UI needs to stay in sync with data

**Solution:** Observer pattern
```python
class DataStore:
    def __init__(self):
        self.observers = []
        self.data = {}

    def subscribe(self, callback):
        self.observers.append(callback)

    def update_data(self, new_data):
        self.data = new_data
        self.notify_observers()

    def notify_observers(self):
        for callback in self.observers:
            callback(self.data)

# Usage
store = DataStore()
store.subscribe(lambda data: app.update_menu(data))
```

### 3. Error Handling Hierarchy

```
┌─────────────────────────────┐
│  Critical Errors            │
│  (Invalid API token)        │
│  → Show error, exit         │
└─────────────────────────────┘
         ↑
┌─────────────────────────────┐
│  Recoverable Errors         │
│  (Network timeout)          │
│  → Use cache, retry later   │
└─────────────────────────────┘
         ↑
┌─────────────────────────────┐
│  Soft Errors                │
│  (Rate limit hit)           │
│  → Use cache, show warning  │
└─────────────────────────────┘
```

### 4. Threading Model

**Main thread:** UI updates only
**Background thread:** API calls, cache updates

```python
import threading

class TogglMenuBar(rumps.App):
    def __init__(self):
        super().__init__("Toggl")
        self.update_in_background()

    def update_in_background(self):
        """Fetch data in background thread."""
        thread = threading.Thread(target=self.fetch_and_update)
        thread.daemon = True
        thread.start()

    def fetch_and_update(self):
        """Runs in background thread."""
        data = fetch_earnings()

        # Update UI on main thread
        rumps.notification(
            title="Earnings Updated",
            subtitle="",
            message=f"Today: ${data['total']}"
        )
```

## Performance Considerations

### Memory Usage
- Cache size: ~10KB per day of entries
- Monthly cache: ~300KB
- Total app memory: < 50MB (Python + dependencies)

### Startup Time
- Initial launch: < 2 seconds
- Subsequent launches (with cache): < 0.5 seconds

### API Call Budget
```
Daily API calls (worst case):
- Projects: 1 call/day = 1
- Today's entries: 24 calls/day (every hour) = 24
- Background refresh: handled by cache

Total: ~25 calls/day (well under 30/hour limit)
```

## Security Architecture

### 1. Credential Storage

```
Option 1: macOS Keychain (Recommended)
┌─────────────┐
│ Menu Bar App│
└──────┬──────┘
       │ get_password("toggl_api_token")
       ↓
┌─────────────┐
│   Keychain  │
│   Access    │
└─────────────┘

Option 2: Encrypted config file
┌─────────────┐
│ Menu Bar App│
└──────┬──────┘
       │ decrypt(config.enc)
       ↓
┌─────────────┐
│  AES-256    │
│  Encryption │
└─────────────┘
```

### 2. Network Security
- Always use HTTPS
- Validate SSL certificates
- Timeout after 10 seconds
- No sensitive data in logs

## Deployment

### Development
```bash
# Run from source
source venv/bin/activate
python app.py
```

### Production
```bash
# Build .app bundle
python setup.py py2app

# Output: dist/Toggl Earnings.app
# Size: ~20-30MB

# Install: Copy to /Applications
cp -r "dist/Toggl Earnings.app" /Applications/
```

### Auto-start on Login
```python
# Add to login items via LaunchAgent
~/Library/LaunchAgents/com.toggl.menubar.plist
```

## Monitoring & Logging

```python
# Log structure
logs/
├── app.log           # General app logs
├── api.log           # API requests/responses
└── errors.log        # Errors only

# Log rotation: Keep last 7 days
# Log level: INFO in production, DEBUG in development
```

## Testing Strategy

### Unit Tests
- Test earnings calculations
- Test cache logic
- Test API client
- Test date helpers

### Integration Tests
- Test API → Cache → Calculator flow
- Test error handling paths
- Test rate limiting

### Manual Testing
- Install .app on clean macOS
- Test with invalid API token
- Test with no internet
- Test menu interactions
- Test auto-refresh

## Rollout Plan

### Phase 1: Local Development
- Build and test on your machine
- Verify all features work
- Check performance

### Phase 2: Beta Testing
- Package as .app
- Test on another Mac (if available)
- Collect feedback

### Phase 3: Production
- Final polish
- Create installer (optional)
- Write user documentation

## Maintenance

### Regular Tasks
- Update dependencies monthly
- Check for Toggl API changes
- Clear old cache files
- Monitor error logs

### Version Updates
```
1.0.0 - MVP (daily/weekly/monthly earnings)
1.1.0 - Add notifications, goals
1.2.0 - Add start/stop timer
2.0.0 - Full rewrite in Swift (future)
```
