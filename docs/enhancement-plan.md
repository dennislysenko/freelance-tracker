# Implementation Plan: Freelance Tracker Enhancements

**Date:** 2025-12-05
**Status:** Planning
**Priority:** High (Dock Icon Fix is Critical)

## Executive Summary

This plan addresses four critical requirements for the Freelance Tracker macOS menu bar app:
1. **Remove Python dock icon** (highest priority, previous attempts failed)
2. **Simple preferences UI** (open JSON in text editor)
3. **Settings categorization** (which settings to expose, validation rules)
4. **QA process design** (automated tests + human verification)

## Current Architecture Analysis

**Tech Stack:**
- Python 3.14
- rumps framework for menu bar
- LaunchAgent service (com.freelancetracker.menubar.plist)
- Preferences: JSON at `~/Library/Application Support/TogglMenuBar/preferences.json`
- Cache: `~/Library/Caches/TogglMenuBar/`
- Logs: `~/Library/Logs/freelancetracker-*.log`

**Current State:**
- Running as LaunchAgent with `ProcessType: Interactive`
- No Info.plist configuration
- No LSUIElement setting
- Python script executed directly (not bundled as .app)
- Manual restart required after preference changes

---

## 1. CRITICAL: Remove Python Dock Icon

### Problem Analysis
Previous attempts failed because the script runs directly via Python interpreter without proper app bundle configuration. The LaunchAgent plist has `ProcessType: Interactive` but no LSUIElement setting, and rumps doesn't automatically suppress the dock icon when running as a raw Python script.

### Solution 1: AppKit LSUIElement (Recommended First Try)
**Approach:** Set LSUIElement programmatically at runtime using AppKit

**Pros:**
- Minimal changes (3-5 lines of code)
- No build process required
- Works with existing LaunchAgent setup
- Fastest to implement and test
- Can be reverted instantly

**Cons:**
- May not work reliably across macOS versions
- Depends on PyObjC being available
- Previous attempts may have tried this

**Implementation:**
```python
# Add to menubar_app.py BEFORE creating FreelanceTrackerApp
import rumps
from AppKit import NSBundle

# Hide dock icon
bundle = NSBundle.mainBundle()
info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
info['LSUIElement'] = '1'

class FreelanceTrackerApp(rumps.App):
    # ... existing code
```

**Verification:**
1. User restarts service: `./restart_service.sh`
2. User checks dock visually (MANUAL - cannot be automated)
3. User confirms menu bar icon still works

---

### Solution 2: py2app Bundling (Backup Solution)
**Approach:** Bundle as proper .app with Info.plist containing LSUIElement

**Pros:**
- Most reliable, standard macOS approach
- Proper app bundle structure
- Can set LSUIElement in Info.plist permanently
- Works 100% when configured correctly

**Cons:**
- Requires build step (adds complexity)
- Need to modify LaunchAgent plist to point to .app
- Need to update all management scripts
- Longer implementation time
- More moving parts (higher risk of new issues)

**Implementation:**

Create `/Users/dennis/dev/freelance-workflow/setup.py`:
```python
from setuptools import setup

APP = ['menubar_app.py']
DATA_FILES = []
OPTIONS = {
    'argv_emulation': False,
    'plist': {
        'LSUIElement': True,
        'CFBundleName': 'Freelance Tracker',
        'CFBundleDisplayName': 'Freelance Tracker',
        'CFBundleIdentifier': 'com.freelancetracker.menubar',
        'CFBundleVersion': '1.0.0',
        'NSRequiresAquaSystemAppearance': False,
    },
    'packages': ['rumps', 'requests', 'dotenv'],
    'includes': ['toggl_data', 'preferences', 'api_audit'],
}

setup(
    name='FreelanceTracker',
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
```

Build script `/Users/dennis/dev/freelance-workflow/build_app.sh`:
```bash
#!/bin/bash
source venv/bin/activate
python setup.py py2app
```

Update LaunchAgent plist:
```xml
<key>ProgramArguments</key>
<array>
    <string>/Users/dennis/dev/freelance-workflow/dist/FreelanceTracker.app/Contents/MacOS/FreelanceTracker</string>
</array>
```

**Verification:**
Same as Solution 1 but also verify .app bundle structure

---

### Solution 3: LaunchAgent ProcessType Adjustment (Low Confidence)
**Approach:** Change ProcessType in plist

**Pros:**
- Zero code changes
- Quick to test

**Cons:**
- ProcessType="Background" may break GUI features
- ProcessType doesn't directly control dock icon
- Unlikely to work based on research

**Implementation:**
Change in `com.freelancetracker.menubar.plist`:
```xml
<key>ProcessType</key>
<string>Background</string>
```

**Verification:**
Same as Solution 1

---

### Recommended Approach Order

1. **Try Solution 1 first** (AppKit LSUIElement) - 30 minutes
   - If fails: Document exact failure mode
   - Check if PyObjC is available

2. **If Solution 1 fails, implement Solution 2** (py2app) - 2-3 hours
   - More robust, industry standard
   - Worth the extra complexity for reliability

3. **Skip Solution 3** unless specifically requested

---

## 2. Simple Preferences UI

### Design: Open JSON in Default Editor

**Implementation Strategy:**
- Add menu item "⚙️ Edit Preferences"
- Use macOS `open` command to open JSON in default editor
- Watch file for changes using file modification time
- Validate JSON after changes detected
- Auto-restart service on successful validation
- Show notification on validation failure

### Code Changes

**File: `/Users/dennis/dev/freelance-workflow/menubar_app.py`**

Add menu item in `update_display()` method (after API Audit Log item):
```python
# After "📋 View API Audit Log"
self.menu.add(rumps.MenuItem("⚙️ Edit Preferences", callback=self.edit_preferences))
```

Add new method to FreelanceTrackerApp class:
```python
@rumps.clicked("⚙️ Edit Preferences")
def edit_preferences(self, _):
    """Open preferences file in default text editor and watch for changes."""
    from preferences import PREFERENCES_FILE
    import subprocess
    import threading

    # Store original modification time
    original_mtime = PREFERENCES_FILE.stat().st_mtime

    # Open in default editor
    try:
        subprocess.run(['open', str(PREFERENCES_FILE)], check=True)

        rumps.notification(
            title="Freelance Tracker",
            subtitle="Preferences Editor",
            message="Edit and save the file. Service will auto-restart on save."
        )

        # Start background thread to watch for changes
        threading.Thread(
            target=self._watch_preferences_changes,
            args=(original_mtime,),
            daemon=True
        ).start()

    except subprocess.CalledProcessError as e:
        rumps.notification(
            title="Freelance Tracker",
            subtitle="Error",
            message=f"Failed to open preferences: {str(e)}"
        )

def _watch_preferences_changes(self, original_mtime):
    """Watch for preference file changes and auto-restart."""
    from preferences import PREFERENCES_FILE, load_preferences
    import time
    import json
    import subprocess

    max_wait = 300  # 5 minutes
    check_interval = 2  # Check every 2 seconds
    elapsed = 0

    while elapsed < max_wait:
        time.sleep(check_interval)
        elapsed += check_interval

        if not PREFERENCES_FILE.exists():
            continue

        current_mtime = PREFERENCES_FILE.stat().st_mtime

        if current_mtime > original_mtime:
            # File was modified, validate and restart
            try:
                # Validate JSON structure
                with open(PREFERENCES_FILE, 'r') as f:
                    prefs = json.load(f)

                # Validate required fields and types
                validation_errors = validate_preferences(prefs)

                if validation_errors:
                    rumps.notification(
                        title="Freelance Tracker",
                        subtitle="Invalid Preferences",
                        message=f"Errors: {', '.join(validation_errors)}. Please fix and save again."
                    )
                    # Reset original_mtime to watch for next save
                    original_mtime = current_mtime
                    continue

                # Valid! Show success and restart
                rumps.notification(
                    title="Freelance Tracker",
                    subtitle="Preferences Saved",
                    message="Restarting service with new settings..."
                )

                # Restart service
                time.sleep(1)
                restart_script = Path(__file__).parent / "restart_service.sh"
                subprocess.Popen([str(restart_script)])

                # Exit this app instance (restart script will launch new one)
                rumps.quit_application()

            except json.JSONDecodeError as e:
                rumps.notification(
                    title="Freelance Tracker",
                    subtitle="Invalid JSON",
                    message=f"Syntax error: {str(e)}. Please fix and save again."
                )
                original_mtime = current_mtime
                continue

            except Exception as e:
                rumps.notification(
                    title="Freelance Tracker",
                    subtitle="Error",
                    message=f"Validation failed: {str(e)}"
                )
                original_mtime = current_mtime
                continue
```

### Validation Function

**File: `/Users/dennis/dev/freelance-workflow/preferences.py`**

Add validation function:
```python
def validate_preferences(prefs):
    """
    Validate preferences structure and types.
    Returns list of error messages (empty if valid).
    """
    errors = []

    # Required fields with type checks
    required_fields = {
        'refresh_interval': (int, lambda x: x > 0, "must be positive integer"),
        'show_notifications': (bool, None, None),
        'daily_goal': (int, lambda x: x >= 0, "must be non-negative"),
        'weekly_goal': (int, lambda x: x >= 0, "must be non-negative"),
        'monthly_goal': (int, lambda x: x >= 0, "must be non-negative"),
        'show_hours': (bool, None, None),
        'menu_bar_format': (str, lambda x: '${total' in x, "must contain ${total}"),
        'auto_start': (bool, None, None),
        'cache_ttl_projects': (int, lambda x: x > 0, "must be positive integer"),
        'cache_ttl_today': (int, lambda x: x > 0, "must be positive integer"),
        'vacation_days_per_month': (int, lambda x: 0 <= x <= 31, "must be 0-31"),
    }

    for field, (expected_type, validator, error_msg) in required_fields.items():
        if field not in prefs:
            errors.append(f"Missing required field: {field}")
            continue

        value = prefs[field]

        # Type check
        if not isinstance(value, expected_type):
            errors.append(f"{field}: expected {expected_type.__name__}, got {type(value).__name__}")
            continue

        # Custom validation
        if validator and not validator(value):
            errors.append(f"{field}: {error_msg}")

    # Optional fields with type checks
    if 'project_targets' in prefs:
        if not isinstance(prefs['project_targets'], dict):
            errors.append("project_targets must be an object")
        else:
            for proj, hours in prefs['project_targets'].items():
                if not isinstance(hours, (int, float)):
                    errors.append(f"project_targets.{proj} must be a number")
                elif hours < 0:
                    errors.append(f"project_targets.{proj} must be non-negative")

    return errors
```

### Project Targets (Billable and Non-Billable)

The `project_targets` setting accepts monthly hour targets for any project (billable or non-billable):

- **Billable projects**: Always appear in monthly view, target is optional
- **Non-billable projects**: Only appear in monthly view if target is configured
- **Display format**: Projects with targets show "Name: 7.5h / 10h (75%)"

**Example configuration:**
```json
"project_targets": {
  "Weather Optics": 40,      // Billable project with target
  "Sanction Search": 10       // Non-billable project with target
}
```

**Validation rules**: Target values must be non-negative numbers.

**Daily view behavior**: All projects with logged time appear (billable show earnings, non-billable show hours only).

**Monthly view behavior**: Billable projects always appear; non-billable projects only appear if they have a configured target.
```

---

## 3. Settings Categorization

### User-Friendly Settings (Easy to Edit)

These should be clearly documented in the preferences file:

**Display Settings:**
- `menu_bar_format`: Format string for menu bar display (default: "💰 ${total:.0f}")
- `show_hours`: Show hours in display (boolean)
- `show_notifications`: Enable notification popups (boolean)

**Goals & Targets:**
- `daily_goal`: Daily earnings target in dollars (integer)
- `weekly_goal`: Weekly earnings target (integer)
- `monthly_goal`: Monthly earnings target (integer)
- `vacation_days_per_month`: PTO days to exclude from projections (0-31)
- `project_targets`: Monthly hour targets by project name (object, e.g., {"ProjectName": 30})

**Refresh Settings:**
- `refresh_interval`: Auto-refresh interval in seconds (300-3600, default 1800)
- `cache_ttl_today`: Today's data cache lifetime in seconds (default 1800)

### Advanced Settings (Less Common Changes)

**System Settings:**
- `auto_start`: Auto-start on login (boolean, requires service reinstall)
- `cache_ttl_projects`: Project cache lifetime in seconds (default 86400)

### Hidden Settings (Future Use)

These might be added later but not exposed initially:
- API endpoint URLs (if custom Toggl instance)
- Debug logging level
- Custom date formats
- Timezone overrides

### Preferences Documentation

Add inline comments to help users understand settings when they open the file:

**Example enhanced preferences.json:**
```json
{
  "_comment": "Freelance Tracker Settings - Edit carefully, service auto-restarts on save",

  "menu_bar_format": "💰 ${total:.0f}",
  "show_hours": true,
  "show_notifications": true,

  "daily_goal": 400,
  "weekly_goal": 2000,
  "monthly_goal": 8000,
  "vacation_days_per_month": 4,
  "project_targets": {
    "Weather Optics": 30,
    "_note": "Add project names as keys with monthly hour targets as values"
  },

  "refresh_interval": 1800,
  "cache_ttl_today": 1800,

  "auto_start": true,
  "cache_ttl_projects": 86400
}
```

---

## 4. QA Process Design

### Testing Strategy

#### A. Unit Tests (Automated)

**Test Framework:** pytest

**New File: `/Users/dennis/dev/freelance-workflow/tests/test_preferences.py`**

```python
import pytest
import json
from pathlib import Path
from preferences import validate_preferences, load_preferences, save_preferences

class TestPreferencesValidation:
    def test_valid_preferences(self):
        """Test that valid preferences pass validation."""
        valid_prefs = {
            "refresh_interval": 1800,
            "show_notifications": True,
            "daily_goal": 400,
            "weekly_goal": 2000,
            "monthly_goal": 8000,
            "show_hours": True,
            "menu_bar_format": "💰 ${total:.0f}",
            "auto_start": True,
            "cache_ttl_projects": 86400,
            "cache_ttl_today": 1800,
            "vacation_days_per_month": 4,
            "project_targets": {"TestProject": 30}
        }
        errors = validate_preferences(valid_prefs)
        assert errors == []

    def test_missing_required_field(self):
        """Test that missing required fields are caught."""
        incomplete_prefs = {"refresh_interval": 1800}
        errors = validate_preferences(incomplete_prefs)
        assert len(errors) > 0
        assert any("Missing required field" in e for e in errors)

    def test_invalid_types(self):
        """Test that type mismatches are caught."""
        # Test each field with wrong type
        pass

    def test_invalid_ranges(self):
        """Test that out-of-range values are caught."""
        # Test boundary conditions
        pass

    def test_invalid_vacation_days(self):
        """Test vacation_days_per_month range."""
        # Test values outside 0-31
        pass
```

**New File: `/Users/dennis/dev/freelance-workflow/tests/test_toggl_data.py`**

```python
import pytest
from datetime import datetime, timedelta
from toggl_data import (
    calculate_business_days,
    calculate_monthly_projection,
    get_worked_days_this_month
)

class TestBusinessDays:
    def test_business_days_calculation(self):
        """Test business day counting."""
        # January 2025 has 23 business days (31 days, starting Wednesday)
        result = calculate_business_days(2025, 1)
        assert result == 23

    def test_february_2025(self):
        """Test February 2025 (28 days, starting Saturday)."""
        result = calculate_business_days(2025, 2)
        assert result == 20

class TestProjections:
    def test_projection_with_no_worked_days(self):
        """Test projection when no days have been worked."""
        # Test empty state handling
        pass
```

**New File: `/Users/dennis/dev/freelance-workflow/tests/test_cache.py`**

```python
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from toggl_data import cache_entries, get_cached_entries
from preferences import CACHE_DIR

class TestCaching:
    def test_cache_write_read(self, tmp_path):
        """Test writing and reading cache."""
        # Test cache operations
        pass

    def test_cache_expiry(self):
        """Test that stale cache is ignored."""
        # Test TTL logic
        pass

    def test_cache_invalidation(self):
        """Test cache invalidation on date change."""
        # Test daily cache key changes
        pass
```

#### B. Integration Tests (Semi-Automated)

**New File: `/Users/dennis/dev/freelance-workflow/tests/test_integration.py`**

```python
import pytest
from menubar_app import FreelanceTrackerApp

class TestMenuBarIntegration:
    def test_app_initialization(self):
        """Test that app initializes without errors."""
        # Can't fully test GUI, but can test object creation
        app = FreelanceTrackerApp()
        assert app.title is not None

    def test_update_display_no_crash(self):
        """Test update_display doesn't crash."""
        app = FreelanceTrackerApp()
        # Mock API responses...
        app.update_display()
        # Should not raise exception
```

#### C. Manual Testing Checklist (Human Verification)

**Test Plan Document: `/Users/dennis/dev/freelance-workflow/docs/manual-test-checklist.md`**

```markdown
# Manual Testing Checklist

## Dock Icon Verification (CRITICAL - Cannot be automated)
- [ ] Restart service: `./restart_service.sh`
- [ ] Wait 5 seconds
- [ ] Check Dock: Python icon should NOT appear
- [ ] Check Menu Bar: 💰 icon SHOULD appear
- [ ] Click menu bar icon: Menu should open normally
- [ ] Test all menu items work
- [ ] Check Activity Monitor: app should be running
- [ ] Repeat test after system reboot

## Preferences UI Testing
- [ ] Click "⚙️ Edit Preferences"
- [ ] Notification appears
- [ ] Preferences file opens in text editor
- [ ] Make valid change (e.g., daily_goal: 500)
- [ ] Save file
- [ ] Within 5 seconds: "Preferences Saved" notification
- [ ] App restarts automatically
- [ ] Menu displays updated value
- [ ] Test invalid JSON: Missing comma
  - [ ] Save file
  - [ ] "Invalid JSON" notification appears
  - [ ] Fix and save again
  - [ ] App restarts successfully
- [ ] Test invalid value: vacation_days_per_month: 100
  - [ ] "Invalid Preferences" notification
  - [ ] Lists specific error
  - [ ] Fix and save again
  - [ ] App restarts successfully

## Display & Functionality
- [ ] Menu bar shows correct daily total
- [ ] Click to open menu: All sections present
- [ ] Project breakdown shows correctly
- [ ] Weekly summary displays
- [ ] Monthly projection shows
- [ ] "Refresh Now" works (check API call count)
- [ ] "Refresh Projects" works
- [ ] "View API Audit Log" opens terminal
- [ ] Quit button exits app

## Performance & Stability
- [ ] Check memory usage: `./status_service.sh`
- [ ] Should be < 100MB
- [ ] Leave running for 1 hour
- [ ] Auto-refresh should occur (check timestamp)
- [ ] No crash or memory leak
- [ ] Check logs: `./logs.sh`
- [ ] No error messages

## Service Management
- [ ] `./restart_service.sh` - works
- [ ] `./status_service.sh` - shows running
- [ ] `./uninstall_service.sh` - stops service
- [ ] `./install_service.sh` - reinstalls
- [ ] Auto-start after reboot (if auto_start: true)
```

#### D. Automated Test Runner

**New File: `/Users/dennis/dev/freelance-workflow/run_tests.sh`**

```bash
#!/bin/bash
# Run automated test suite

echo "Running Freelance Tracker Test Suite..."
echo "========================================"

# Activate venv
source venv/bin/activate

# Install test dependencies if needed
pip install pytest pytest-cov 2>/dev/null

# Run tests with coverage
pytest tests/ -v --cov=. --cov-report=term-missing

# Check exit code
if [ $? -eq 0 ]; then
    echo ""
    echo "✓ All automated tests passed!"
    echo ""
    echo "Next steps:"
    echo "1. Review docs/manual-test-checklist.md"
    echo "2. Perform manual verification (especially dock icon)"
    echo "3. Deploy changes"
else
    echo ""
    echo "✗ Tests failed. Please fix before deploying."
    exit 1
fi
```

#### E. CI/CD Considerations

Since this is a local development project, full CI/CD may be overkill, but consider:

**GitHub Actions (Optional):**
```yaml
# .github/workflows/test.yml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.14'
      - name: Install dependencies
        run: |
          python -m venv venv
          source venv/bin/activate
          pip install -r requirements.txt
          pip install pytest pytest-cov
      - name: Run tests
        run: |
          source venv/bin/activate
          pytest tests/ -v
```

---

## 5. Implementation Order

### Phase 1: Dock Icon Fix (Priority: CRITICAL)
**Estimated Time: 30 minutes - 3 hours**

1. Implement Solution 1 (AppKit LSUIElement) - 30 min
   - Add LSUIElement code to menubar_app.py
   - Test with `./restart_service.sh`
   - User manually verifies dock icon hidden
   - If successful: DONE
   - If fails: Proceed to step 2

2. If Solution 1 fails: Implement Solution 2 (py2app) - 2-3 hours
   - Create setup.py
   - Create build_app.sh
   - Update LaunchAgent plist
   - Update all management scripts
   - Build and test
   - User manually verifies

3. Document exact configuration used
   - Update README.md with setup steps
   - Note any platform-specific issues

**Human Verification Required:** Dock icon visibility (cannot be checked remotely)

---

### Phase 2: Preferences UI (Priority: HIGH)
**Estimated Time: 2-3 hours**

1. Add validation function to preferences.py - 45 min
   - Implement `validate_preferences()`
   - Write validation rules for all fields
   - Test with various inputs

2. Add "Edit Preferences" menu item - 1 hour
   - Implement `edit_preferences()` callback
   - Implement `_watch_preferences_changes()` method
   - Test file watching logic
   - Test notification system

3. Test full workflow - 30 min
   - Open preferences
   - Make valid change
   - Verify auto-restart
   - Test invalid JSON handling
   - Test invalid value handling

4. Documentation - 30 min
   - Update README with preferences editing workflow
   - Document all settings and their valid ranges
   - Add troubleshooting section

**Human Verification Required:**
- Preferences editor opens in correct application
- Notifications appear with correct messages
- Auto-restart works smoothly

---

### Phase 3: Testing Infrastructure (Priority: MEDIUM)
**Estimated Time: 3-4 hours**

1. Set up test structure - 30 min
   - Create tests/ directory
   - Create __init__.py
   - Install pytest
   - Configure pytest.ini

2. Write unit tests - 2 hours
   - test_preferences.py (validation tests)
   - test_toggl_data.py (calculation tests)
   - test_cache.py (caching tests)

3. Create manual test checklist - 30 min
   - Document all manual verification steps
   - Create checklist for dock icon
   - Create checklist for preferences UI
   - Create checklist for display/functionality

4. Create test runner script - 30 min
   - Write run_tests.sh
   - Test execution
   - Verify output

5. Initial test run - 30 min
   - Run all tests
   - Fix any failures
   - Verify coverage

**Human Verification Required:**
- Review manual test checklist completeness
- Perform full manual test pass

---

### Phase 4: Documentation & Polish (Priority: LOW)
**Estimated Time: 1-2 hours**

1. Update README.md - 30 min
   - Document new features
   - Update troubleshooting section
   - Add testing instructions

2. Update CLAUDE.md - 30 min
   - Document new files
   - Update common commands
   - Update typical workflow

3. Create preferences documentation - 30 min
   - Document each setting
   - Provide examples
   - Note valid ranges

**Human Verification Required:**
- Review documentation for accuracy
- Verify examples work

---

## 6. Risk Analysis & Mitigation

### Risk 1: Dock Icon Fix Fails Again
**Probability:** Medium
**Impact:** High
**Mitigation:**
- Try both solutions in sequence
- If both fail, research platform-specific macOS 14+ issues
- Consider asking user to provide macOS version details
- Last resort: Accept dock icon, focus on other improvements

### Risk 2: Auto-Restart Causes Service Loop
**Probability:** Low
**Impact:** High
**Mitigation:**
- Add 1-second delay before restart
- Use `subprocess.Popen()` instead of direct restart
- Test with invalid preferences multiple times
- Add timeout to file watcher (5 minutes max)

### Risk 3: File Watcher Misses Changes
**Probability:** Low
**Impact:** Medium
**Mitigation:**
- Check file modification time every 2 seconds
- Watch for up to 5 minutes after opening
- Show clear notification when watching starts
- User can manually restart if needed

### Risk 4: Test Suite Incomplete
**Probability:** Medium
**Impact:** Low
**Mitigation:**
- Prioritize critical path testing
- Clear manual test checklist for gaps
- Iterate on tests over time
- Start minimal, expand later

### Risk 5: Breaking Existing Functionality
**Probability:** Low
**Impact:** High
**Mitigation:**
- Test thoroughly before each commit
- Keep git history clean for easy rollback
- User can quickly revert via git
- All changes are additive (minimal modification of existing code)

---

## 7. Success Criteria

### Must Have (Blocking Launch)
- [ ] Python dock icon is hidden (verified visually by user)
- [ ] Menu bar icon still appears and works
- [ ] "Edit Preferences" menu item works
- [ ] Preferences open in text editor
- [ ] Invalid JSON shows error notification
- [ ] Valid changes trigger auto-restart
- [ ] No crashes or errors in logs

### Should Have (Nice to Have)
- [ ] Comprehensive unit test coverage (>70%)
- [ ] Manual test checklist completed
- [ ] All validation rules working
- [ ] Documentation updated
- [ ] Clean git history

### Could Have (Future Enhancements)
- [ ] CI/CD pipeline
- [ ] GUI preferences editor (native)
- [ ] More sophisticated validation (ranges, dependencies)
- [ ] A/B testing of different dock icon solutions

---

## 8. Critical Files for Implementation

Here are the key files that will be modified or created:

**Modified:**
- `/Users/dennis/dev/freelance-workflow/menubar_app.py` - Add LSUIElement code, Edit Preferences menu item, file watching
- `/Users/dennis/dev/freelance-workflow/preferences.py` - Add `validate_preferences()` function
- `/Users/dennis/dev/freelance-workflow/com.freelancetracker.menubar.plist` - May need updates for py2app

**Created (if py2app needed):**
- `/Users/dennis/dev/freelance-workflow/setup.py` - py2app configuration
- `/Users/dennis/dev/freelance-workflow/build_app.sh` - Build script

**Created (testing):**
- `/Users/dennis/dev/freelance-workflow/tests/__init__.py`
- `/Users/dennis/dev/freelance-workflow/tests/test_preferences.py`
- `/Users/dennis/dev/freelance-workflow/tests/test_toggl_data.py`
- `/Users/dennis/dev/freelance-workflow/tests/test_cache.py`
- `/Users/dennis/dev/freelance-workflow/tests/test_integration.py`
- `/Users/dennis/dev/freelance-workflow/run_tests.sh`
- `/Users/dennis/dev/freelance-workflow/docs/manual-test-checklist.md`

---

## 9. Next Steps

1. **Review this plan** and approve approach
2. **Start Phase 1**: Implement dock icon fix (Solution 1 first)
3. **Manual verification**: User checks dock icon visibility
4. **Continue to Phase 2**: Implement preferences UI
5. **Test thoroughly**: Run automated tests + manual checklist
6. **Deploy**: Restart service with all changes

---

## Research Sources

- [GitHub - jaredks/rumps: Ridiculously Uncomplicated macOS Python Statusbar apps](https://github.com/jaredks/rumps)
- [Stopping the Python rocketship icon - All this](https://leancrew.com/all-this/2014/01/stopping-the-python-rocketship-icon/)
- [How to hide application icon from Mac OS X dock - Stack Overflow](https://stackoverflow.com/questions/4345102/how-to-hide-application-icon-from-mac-os-x-dock)
- [How to hide the Dock icon - Stack Overflow](https://stackoverflow.com/questions/620841/how-to-hide-the-dock-icon)
- [Open document with default OS application in Python - Stack Overflow](https://stackoverflow.com/questions/434597/open-document-with-default-os-application-in-python-both-in-windows-and-mac-os)

---

**Last Updated:** 2025-12-05
**Status:** Awaiting approval to begin implementation
