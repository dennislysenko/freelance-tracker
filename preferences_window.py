"""Native macOS preferences window for Freelance Tracker."""

import json
from AppKit import (
    NSWindow, NSApp, NSApplication, NSTextField, NSButton, NSAlert,
    NSMakeRect, NSPanel, NSBackingStoreBuffered, NSView, NSFont, NSScreen,
    NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable, NSSwitchButton, NSOnState, NSOffState,
    NSNumberFormatter, NSAlertFirstButtonReturn, NSTabView, NSTabViewItem
)
from preferences import (
    load_preferences, save_preferences, validate_preferences, DEFAULT_PREFERENCES
)
import rumps


class PreferencesWindowController:
    """
    Manages the preferences window lifecycle and UI interactions.
    Singleton pattern to prevent multiple windows.
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.window = None
        self.widgets = {}
        self.current_prefs = load_preferences()

    def show_window(self):
        """Show preferences window (create if needed)."""
        if self.window is None:
            self._create_window()
        else:
            self._load_current_values()

        self.window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    def _create_window(self):
        """Create the NSWindow with tabbed interface."""
        # Center window on screen (600x500)
        screen_frame = NSScreen.mainScreen().frame()
        window_width, window_height = 600, 500
        window_x = (screen_frame.size.width - window_width) / 2
        window_y = (screen_frame.size.height - window_height) / 2

        frame = NSMakeRect(window_x, window_y, window_width, window_height)

        # Window style
        style = (NSWindowStyleMaskTitled |
                 NSWindowStyleMaskClosable |
                 NSWindowStyleMaskMiniaturizable)

        # Create window as NSPanel (stays on top)
        self.window = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            frame,
            style,
            NSBackingStoreBuffered,
            False
        )

        self.window.setTitle_("Freelance Tracker Preferences")

        # Get content view
        content_view = self.window.contentView()

        # Create tab view
        tab_view = NSTabView.alloc().initWithFrame_(NSMakeRect(20, 60, 560, 400))

        # Create 2 tabs
        self._create_caching_tab(tab_view)
        self._create_work_planning_tab(tab_view)

        content_view.addSubview_(tab_view)

        # Create bottom buttons
        self._create_bottom_buttons(content_view)

    def _create_bottom_buttons(self, parent_view):
        """Create Reset, Cancel, and Save buttons at bottom."""
        # Save button (right side)
        save_btn = NSButton.alloc().initWithFrame_(NSMakeRect(480, 20, 100, 32))
        save_btn.setTitle_("Save")
        save_btn.setBezelStyle_(1)  # Rounded
        save_btn.setTarget_(self)
        save_btn.setAction_("handleSave:")
        parent_view.addSubview_(save_btn)

        # Cancel button (middle-right)
        cancel_btn = NSButton.alloc().initWithFrame_(NSMakeRect(370, 20, 100, 32))
        cancel_btn.setTitle_("Cancel")
        cancel_btn.setBezelStyle_(1)
        cancel_btn.setTarget_(self)
        cancel_btn.setAction_("handleCancel:")
        parent_view.addSubview_(cancel_btn)

        # Reset button (left side)
        reset_btn = NSButton.alloc().initWithFrame_(NSMakeRect(20, 20, 150, 32))
        reset_btn.setTitle_("Reset to Defaults")
        reset_btn.setBezelStyle_(1)
        reset_btn.setTarget_(self)
        reset_btn.setAction_("handleReset:")
        parent_view.addSubview_(reset_btn)

    def _create_caching_tab(self, tab_view):
        """Tab 3: Caching."""
        tab = NSTabViewItem.alloc().initWithIdentifier_("caching")
        tab.setLabel_("Caching")
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 560, 370))

        y = 320

        self.widgets['cache_ttl_projects'] = self._create_number_field(
            view, "Projects Cache TTL (sec):", 20, y,
            self.current_prefs['cache_ttl_projects'], min_value=1
        )
        y -= 40

        self.widgets['cache_ttl_today'] = self._create_number_field(
            view, "Today Cache TTL (sec):", 20, y,
            self.current_prefs['cache_ttl_today'], min_value=1
        )

        tab.setView_(view)
        tab_view.addTabViewItem_(tab)

    def _create_work_planning_tab(self, tab_view):
        """Tab 2: Work Planning."""
        tab = NSTabViewItem.alloc().initWithIdentifier_("work")
        tab.setLabel_("Work Planning")
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 560, 370))

        y = 320

        # Vacation days
        self.widgets['vacation_days'] = self._create_number_field(
            view, "Vacation Days/Month:", 20, y,
            self.current_prefs['vacation_days_per_month'], min_value=0
        )
        y -= 60

        # Project targets header
        label = NSTextField.alloc().initWithFrame_(NSMakeRect(20, y, 500, 20))
        label.setStringValue_("Project Targets (monthly hour goals):")
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setFont_(NSFont.boldSystemFontOfSize_(12))
        view.addSubview_(label)
        y -= 30

        # 5 project target rows
        project_targets = self.current_prefs.get('project_targets', {})
        projects_list = list(project_targets.items())

        for i in range(5):
            name = projects_list[i][0] if i < len(projects_list) else ""
            hours = projects_list[i][1] if i < len(projects_list) else 0

            # Project name field
            name_field = NSTextField.alloc().initWithFrame_(NSMakeRect(40, y, 280, 25))
            name_field.setStringValue_(name)
            name_field.setPlaceholderString_(f"Project {i+1} name")
            view.addSubview_(name_field)
            self.widgets[f'project_name_{i}'] = name_field

            # Hours field
            hours_field = NSTextField.alloc().initWithFrame_(NSMakeRect(330, y, 80, 25))
            hours_field.setIntValue_(hours if name else 0)
            hours_field.setPlaceholderString_("hours")
            formatter = NSNumberFormatter.alloc().init()
            formatter.setMinimum_(0)
            hours_field.setFormatter_(formatter)
            view.addSubview_(hours_field)
            self.widgets[f'project_hours_{i}'] = hours_field

            y -= 35

        tab.setView_(view)
        tab_view.addTabViewItem_(tab)

    def _create_number_field(self, parent, label_text, x, y, default_value, min_value=None):
        """Helper: create labeled number field."""
        # Label
        label = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, 220, 20))
        label.setStringValue_(label_text)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        parent.addSubview_(label)

        # Input field
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(x + 230, y - 3, 150, 25))

        # Number formatter
        formatter = NSNumberFormatter.alloc().init()
        if min_value is not None:
            formatter.setMinimum_(min_value)

        field.setFormatter_(formatter)
        field.setIntValue_(default_value)
        parent.addSubview_(field)

        return field

    def _create_text_field(self, parent, label_text, x, y, default_value):
        """Helper: create labeled text field."""
        # Label
        label = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, 220, 20))
        label.setStringValue_(label_text)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        parent.addSubview_(label)

        # Input field
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(x + 230, y - 3, 250, 25))
        field.setStringValue_(default_value)
        parent.addSubview_(field)

        return field

    def _create_checkbox(self, parent, label_text, x, y, checked):
        """Helper: create checkbox."""
        checkbox = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, 400, 25))
        checkbox.setButtonType_(NSSwitchButton)
        checkbox.setTitle_(label_text)
        checkbox.setState_(NSOnState if checked else NSOffState)
        parent.addSubview_(checkbox)

        return checkbox

    def _load_current_values(self):
        """Load current preferences into UI widgets."""
        self.current_prefs = load_preferences()

        # Load cache TTL values
        self.widgets['cache_ttl_projects'].setIntValue_(self.current_prefs['cache_ttl_projects'])
        self.widgets['cache_ttl_today'].setIntValue_(self.current_prefs['cache_ttl_today'])

        # Load vacation days
        self.widgets['vacation_days'].setIntValue_(self.current_prefs['vacation_days_per_month'])

        # Load project targets into 5 name/hours field pairs
        project_targets = self.current_prefs.get('project_targets', {})
        projects_list = list(project_targets.items())

        for i in range(5):
            if i < len(projects_list):
                name, hours = projects_list[i]
                self.widgets[f'project_name_{i}'].setStringValue_(name)
                self.widgets[f'project_hours_{i}'].setIntValue_(hours)
            else:
                self.widgets[f'project_name_{i}'].setStringValue_("")
                self.widgets[f'project_hours_{i}'].setIntValue_(0)

    def handleSave_(self, sender):
        """Save button clicked."""
        # Build project_targets dict from 5 name/hours field pairs
        project_targets = {}
        for i in range(5):
            name = self.widgets[f'project_name_{i}'].stringValue().strip()
            hours = self.widgets[f'project_hours_{i}'].intValue()
            if name:  # Only add if name is not empty
                project_targets[name] = hours

        new_prefs = {
            'cache_ttl_projects': self.widgets['cache_ttl_projects'].intValue(),
            'cache_ttl_today': self.widgets['cache_ttl_today'].intValue(),
            'vacation_days_per_month': self.widgets['vacation_days'].intValue(),
            'project_targets': project_targets
        }

        # Validate using existing function
        errors = validate_preferences(new_prefs)

        if errors:
            # Show error dialog
            error_text = "\n".join(errors[:5])  # Show first 5 errors
            if len(errors) > 5:
                error_text += f"\n... and {len(errors) - 5} more errors"

            alert = NSAlert.alloc().init()
            alert.setMessageText_("Invalid Preferences")
            alert.setInformativeText_(error_text)
            alert.addButtonWithTitle_("OK")
            alert.runModal()
            return

        # Save
        save_preferences(new_prefs)

        # Success notification
        rumps.notification(
            title="Freelance Tracker",
            subtitle="Preferences Saved",
            message="Settings updated successfully"
        )

        # Close window
        self.window.close()

        # Trigger app reload (will be implemented in menubar_app.py)
        try:
            NSApp.delegate().app.update_display()
        except:
            pass  # Gracefully handle if app not available

    def handleCancel_(self, sender):
        """Cancel button clicked."""
        self.window.close()

    def handleReset_(self, sender):
        """Reset to defaults button."""
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Reset to Defaults?")
        alert.setInformativeText_("This will restore all settings to default values.")
        alert.addButtonWithTitle_("Reset")
        alert.addButtonWithTitle_("Cancel")

        response = alert.runModal()
        if response == NSAlertFirstButtonReturn:
            # Load defaults into widgets
            self.widgets['cache_ttl_projects'].setIntValue_(DEFAULT_PREFERENCES['cache_ttl_projects'])
            self.widgets['cache_ttl_today'].setIntValue_(DEFAULT_PREFERENCES['cache_ttl_today'])
            self.widgets['vacation_days'].setIntValue_(DEFAULT_PREFERENCES['vacation_days_per_month'])

            # Clear all project target fields
            for i in range(5):
                self.widgets[f'project_name_{i}'].setStringValue_("")
                self.widgets[f'project_hours_{i}'].setIntValue_(0)
