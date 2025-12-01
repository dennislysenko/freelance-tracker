# Toggl Menu Bar App Documentation

Welcome to the Toggl Menu Bar App documentation! This collection of documents will guide you through building a native macOS menu bar application to track your freelance earnings in real-time.

## 📚 Documentation Index

### 1. [Menu Bar App Plan](menubar-app-plan.md)
**Start here!** Complete implementation plan including:
- Core features and UI mockup
- Technology stack comparison (Python + rumps vs Swift)
- Step-by-step implementation guide
- Code snippets to get started
- Packaging and distribution
- Timeline estimate (~10-12 hours for MVP)

### 2. [API Integration Reference](api-integration.md)
Comprehensive guide to the Toggl Track API:
- All key endpoints with examples
- Authentication methods
- Rate limiting strategies (30 requests/hour)
- Best practices for menu bar apps
- Error handling patterns
- Sample code for common operations

### 3. [Architecture Documentation](architecture.md)
Deep dive into the technical architecture:
- Component breakdown and data flow
- Caching strategy to minimize API calls
- File structure and organization
- Threading model
- Security considerations
- Performance optimization
- Testing and deployment strategy

## 🚀 Quick Start

### What You'll Build

A lightweight macOS menu bar app that:
- Shows your **daily earnings** in the menu bar (e.g., "💰 $400")
- Displays a **dropdown menu** with detailed breakdowns
- Shows **weekly and monthly** summaries
- **Auto-refreshes** every 30 minutes
- Uses **smart caching** to stay within API limits
- Looks native and feels snappy

### Technology Choice

**Recommended: Python + rumps**
- Fastest time to MVP (reuse existing script)
- Cross-platform if needed later
- Easy to package as standalone .app

**Alternative: Swift + SwiftUI**
- Native macOS experience
- Smaller bundle size
- Consider for v2.0

## 📋 Implementation Checklist

### Phase 1: Foundation (Day 1-2)
- [ ] Refactor `toggl_earnings.py` into reusable modules
- [ ] Create `toggl_api.py` for API client
- [ ] Create `cache_manager.py` for caching logic
- [ ] Extract `earnings_calculator.py` for business logic

### Phase 2: Menu Bar App (Day 2-3)
- [ ] Install rumps: `pip install rumps`
- [ ] Create basic `app.py` with menu bar icon
- [ ] Implement click handler and menu
- [ ] Display today's earnings in menu bar title
- [ ] Add refresh button

### Phase 3: Data Integration (Day 3)
- [ ] Connect app to Toggl API
- [ ] Fetch and display today's breakdown
- [ ] Add weekly summary menu item
- [ ] Add monthly summary menu item
- [ ] Implement auto-refresh timer

### Phase 4: Polish & Ship (Day 4)
- [ ] Add settings dialog for API token
- [ ] Create app icon
- [ ] Package with py2app
- [ ] Test on clean Mac
- [ ] Add to Login Items (optional)

## 🎨 Visual Design

```
Menu Bar:
┌──────────┐
│ 💰 $400  │ ← Always visible
└──────────┘

Dropdown Menu:
┌─────────────────────────────────────┐
│ 📅 TODAY - Dec 1, 2025              │
│ ───────────────────────────────────│
│   Acme Inc      $225  (1.5h)  │
│   Initech         $150  (1.25h) │
│   Globex      $25  (0.25h) │
│                                     │
│ Total: $400.00 (5.25 hours)         │
│ ═══════════════════════════════════ │
│ 📊 This Week: $400.00               │
│ 📊 This Month: $400.00              │
│ ═══════════════════════════════════ │
│ ⟳ Refresh                           │
│ ⚙ Settings                          │
│ ✕ Quit                              │
└─────────────────────────────────────┘
```

## 💡 Key Features

### Smart Caching
- Historical data cached indefinitely (never changes)
- Today's data refreshed every 30 minutes
- Projects cached for 24 hours
- **Result:** Only 2-3 API calls per day vs 24+ without caching

### Real-time Updates
- Auto-refresh on interval
- Manual refresh button
- Background fetching (non-blocking UI)

### Error Resilience
- Graceful handling of network issues
- Falls back to cached data
- Shows warnings when data is stale
- Never crashes on API errors

## 🔒 Security

- API token stored in macOS Keychain (not in code)
- HTTPS only
- No logging of sensitive data
- Cache encrypted at rest (optional)

## 📦 Distribution

### For Personal Use
```bash
# Build .app
python setup.py py2app

# Copy to Applications
cp -r "dist/Toggl Earnings.app" /Applications/
```

### For Others (Future)
- Notarize with Apple
- Distribute via GitHub Releases
- Consider Mac App Store (requires Apple Developer account)

## 🧪 Testing

### Manual Testing Checklist
- [ ] Menu bar icon shows correct amount
- [ ] Dropdown displays all projects
- [ ] Weekly/monthly summaries accurate
- [ ] Refresh button works
- [ ] Settings persist
- [ ] Works without internet (uses cache)
- [ ] Handles invalid API token gracefully

### Edge Cases
- [ ] No time entries today
- [ ] Projects with no rate
- [ ] Non-billable projects
- [ ] Running timer
- [ ] API rate limit hit

## 🚧 Future Enhancements

### Phase 2
- [ ] Notifications for daily goals
- [ ] Color-coded menu bar icon (green/yellow/red)
- [ ] Quick stats (avg rate, hours today)

### Phase 3
- [ ] Start/stop timer from menu bar
- [ ] Add time entry via quick form
- [ ] Goal progress bar

### Phase 4
- [ ] Historical charts
- [ ] Export to CSV
- [ ] Slack/email notifications
- [ ] Multiple workspaces

## 🔗 Resources

### Documentation
- [rumps GitHub](https://github.com/jaredks/rumps) - Menu bar framework
- [py2app Docs](https://py2app.readthedocs.io/) - App packaging
- [Toggl API v9](https://engineering.toggl.com/docs/api/) - Official API docs

### Existing Work
- `toggl_earnings.py` - Working CLI script
- `.env` - API credentials
- `requirements.txt` - Dependencies

### Design
- [macOS HIG - Menu Bar Extras](https://developer.apple.com/design/human-interface-guidelines/menu-bar-extras)
- [SF Symbols](https://developer.apple.com/sf-symbols/) - Icons

## 🤝 Contributing

This is a personal project, but ideas for enhancements are welcome!

### Development Setup
```bash
# Clone/navigate to repo
cd freelance-workflow

# Activate venv
source venv/bin/activate

# Install additional deps
pip install rumps py2app

# Run in development
python app.py
```

## 📝 License

Private project - all rights reserved.

## ✨ Credits

Built on top of:
- [Toggl Track API](https://toggl.com)
- [rumps](https://github.com/jaredks/rumps) by Jared Suttles
- Python requests library

---

**Next Step:** Read [menubar-app-plan.md](menubar-app-plan.md) to start building!
