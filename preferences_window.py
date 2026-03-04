"""Native macOS preferences window for Freelance Tracker."""

import json
from AppKit import (
    NSWindow, NSApp, NSApplication, NSTextField, NSButton, NSAlert,
    NSMakeRect, NSPanel, NSBackingStoreBuffered, NSView, NSFont, NSScreen,
    NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable, NSSwitchButton, NSOnState, NSOffState,
    NSNumberFormatter, NSAlertFirstButtonReturn, NSTabView, NSTabViewItem,
    NSPopUpButton
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
    PROJECT_TARGET_ROWS = 5
    RETAINER_RATE_ROWS = 5
    PROJECT_DEFINITION_ROWS = 8

    # Maps UI type label → (billing_type, hour_tracking)
    BILLING_TYPE_OPTIONS = [
        ("hourly",          "hourly",          None),
        ("hourly w/ cap",   "hourly_with_cap",  None),
        ("fixed/required",  "fixed_monthly",    "required"),
        ("fixed/soft",      "fixed_monthly",    "soft"),
        ("fixed/none",      "fixed_monthly",    "none"),
    ]

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

        # Create tabs
        self._create_caching_tab(tab_view)
        self._create_work_planning_tab(tab_view)
        self._create_projects_tab(tab_view)

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

        # Project target rows
        project_targets = self.current_prefs.get('project_targets', {})
        projects_list = list(project_targets.items())

        for i in range(self.PROJECT_TARGET_ROWS):
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

    def _get_toggl_project_names(self):
        """Load project names from the Toggl projects cache (alphabetical)."""
        from preferences import CACHE_DIR
        import json as _json
        cache_file = CACHE_DIR / "projects.json"
        if cache_file.exists():
            try:
                with open(cache_file, 'r') as f:
                    projects = _json.load(f)
                return sorted(p['name'] for p in projects.values() if p.get('name'))
            except Exception:
                pass
        return []

    def _create_projects_tab(self, tab_view):
        """Tab: Projects — billing type definitions per project."""
        tab = NSTabViewItem.alloc().initWithIdentifier_("projects")
        tab.setLabel_("Projects")
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 560, 370))

        y = 340

        header = NSTextField.alloc().initWithFrame_(NSMakeRect(10, y, 540, 20))
        header.setStringValue_("Project Billing Definitions")
        header.setBezeled_(False)
        header.setDrawsBackground_(False)
        header.setEditable_(False)
        header.setFont_(NSFont.boldSystemFontOfSize_(12))
        view.addSubview_(header)
        y -= 18

        help_text = NSTextField.alloc().initWithFrame_(NSMakeRect(10, y, 540, 16))
        help_text.setStringValue_(
            "fixed/required: hours carry over monthly  ·  fixed/soft: soft target only  ·  fixed/none: freeform amount"
        )
        help_text.setBezeled_(False)
        help_text.setDrawsBackground_(False)
        help_text.setEditable_(False)
        help_text.setFont_(NSFont.systemFontOfSize_(10))
        view.addSubview_(help_text)
        y -= 28

        # Column headers
        for cx, cw, ct in [(10, 195, "Project"), (210, 130, "Billing Type"), (345, 205, "")]:
            lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(cx, y, cw, 16))
            lbl.setStringValue_(ct)
            lbl.setBezeled_(False)
            lbl.setDrawsBackground_(False)
            lbl.setEditable_(False)
            lbl.setFont_(NSFont.boldSystemFontOfSize_(10))
            view.addSubview_(lbl)
        y -= 20

        projects_config = self.current_prefs.get('projects', {})
        proj_list = list(projects_config.items())
        toggl_names = self._get_toggl_project_names()
        type_labels = [opt[0] for opt in self.BILLING_TYPE_OPTIONS]

        def make_float_field(x, yy, w, value):
            f = NSTextField.alloc().initWithFrame_(NSMakeRect(x, yy, w, 22))
            f.setDoubleValue_(float(value))
            f.setPlaceholderString_("0")
            fmt = NSNumberFormatter.alloc().init()
            fmt.setMinimum_(0)
            fmt.setAllowsFloats_(True)
            fmt.setMinimumFractionDigits_(0)
            fmt.setMaximumFractionDigits_(2)
            f.setFormatter_(fmt)
            view.addSubview_(f)
            return f

        def make_inline_label(x, yy, text):
            lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(x, yy + 2, 50, 18))
            lbl.setStringValue_(text)
            lbl.setBezeled_(False)
            lbl.setDrawsBackground_(False)
            lbl.setEditable_(False)
            lbl.setFont_(NSFont.systemFontOfSize_(11))
            view.addSubview_(lbl)
            return lbl

        for i in range(self.PROJECT_DEFINITION_ROWS):
            defn = proj_list[i][1] if i < len(proj_list) else {}
            proj_name = proj_list[i][0] if i < len(proj_list) else ""

            # Project name dropdown (populated from Toggl cache)
            name_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
                NSMakeRect(10, y, 195, 22), False
            )
            name_popup.addItemWithTitle_("—")
            # Ensure configured name is in list even if not in cache
            names_for_row = list(toggl_names)
            if proj_name and proj_name not in names_for_row:
                names_for_row.insert(0, proj_name)
            for n in names_for_row:
                name_popup.addItemWithTitle_(n)
            name_popup.selectItemWithTitle_(proj_name if proj_name else "—")
            view.addSubview_(name_popup)
            self.widgets[f'pd_name_{i}'] = name_popup

            # Billing type dropdown
            type_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
                NSMakeRect(210, y, 130, 22), False
            )
            for lbl in type_labels:
                type_popup.addItemWithTitle_(lbl)
            type_popup.selectItemWithTitle_(self._defn_to_type_label(defn))
            type_popup.setTarget_(self)
            type_popup.setAction_("handleProjectTypeChange:")
            view.addSubview_(type_popup)
            self.widgets[f'pd_type_{i}'] = type_popup

            # --- Dynamic extra fields (x starts at 345) ---
            # Monthly $ (fixed types)
            lbl_mo = make_inline_label(345, y, "Mo $")
            f_monthly = make_float_field(388, y, 70, defn.get('monthly_amount', 0))
            self.widgets[f'pd_lbl_monthly_{i}'] = lbl_mo
            self.widgets[f'pd_monthly_{i}'] = f_monthly

            # Target h (fixed/required and fixed/soft)
            lbl_tgt = make_inline_label(462, y, "Tgt h")
            f_target = make_float_field(505, y, 45, defn.get('target_hours', 0))
            self.widgets[f'pd_lbl_target_{i}'] = lbl_tgt
            self.widgets[f'pd_target_{i}'] = f_target

            # Rate $/h (hourly_with_cap)
            lbl_rate = make_inline_label(345, y, "Rate")
            f_rate = make_float_field(388, y, 70, defn.get('hourly_rate', 0))
            self.widgets[f'pd_lbl_rate_{i}'] = lbl_rate
            self.widgets[f'pd_rate_{i}'] = f_rate

            # Cap h (hourly_with_cap)
            lbl_cap = make_inline_label(462, y, "Cap h")
            f_cap = make_float_field(505, y, 45, defn.get('cap_hours', 0))
            self.widgets[f'pd_lbl_cap_{i}'] = lbl_cap
            self.widgets[f'pd_cap_{i}'] = f_cap

            # Apply initial visibility
            self._update_project_row_visibility(i)

            y -= 32

        tab.setView_(view)
        tab_view.addTabViewItem_(tab)

    def _update_project_row_visibility(self, i):
        """Show/hide extra fields for row i based on selected billing type."""
        type_popup = self.widgets.get(f'pd_type_{i}')
        if type_popup is None:
            return
        label = type_popup.titleOfSelectedItem()
        billing_type, hour_tracking = self._type_label_to_defn_fields(label)

        show_monthly = billing_type == 'fixed_monthly'
        show_target = billing_type == 'fixed_monthly' and hour_tracking in ('required', 'soft')
        show_rate = billing_type == 'hourly_with_cap'
        show_cap = billing_type == 'hourly_with_cap'

        self.widgets[f'pd_lbl_monthly_{i}'].setHidden_(not show_monthly)
        self.widgets[f'pd_monthly_{i}'].setHidden_(not show_monthly)
        self.widgets[f'pd_lbl_target_{i}'].setHidden_(not show_target)
        self.widgets[f'pd_target_{i}'].setHidden_(not show_target)
        self.widgets[f'pd_lbl_rate_{i}'].setHidden_(not show_rate)
        self.widgets[f'pd_rate_{i}'].setHidden_(not show_rate)
        self.widgets[f'pd_lbl_cap_{i}'].setHidden_(not show_cap)
        self.widgets[f'pd_cap_{i}'].setHidden_(not show_cap)

    def handleProjectTypeChange_(self, sender):
        """Called when a billing type dropdown changes — updates field visibility."""
        for i in range(self.PROJECT_DEFINITION_ROWS):
            if self.widgets.get(f'pd_type_{i}') == sender:
                self._update_project_row_visibility(i)
                break

    def _defn_to_type_label(self, defn):
        """Convert a project definition dict to its UI type label string."""
        billing_type = defn.get('billing_type', 'hourly')
        hour_tracking = defn.get('hour_tracking')
        for label, bt, ht in self.BILLING_TYPE_OPTIONS:
            if bt == billing_type and ht == hour_tracking:
                return label
        return "hourly"

    def _type_label_to_defn_fields(self, label):
        """Convert a UI type label to (billing_type, hour_tracking) tuple."""
        for lbl, bt, ht in self.BILLING_TYPE_OPTIONS:
            if lbl == label:
                return bt, ht
        return "hourly", None

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

        # Load project targets into name/hours field pairs
        project_targets = self.current_prefs.get('project_targets', {})
        projects_list = list(project_targets.items())

        for i in range(self.PROJECT_TARGET_ROWS):
            if i < len(projects_list):
                name, hours = projects_list[i]
                self.widgets[f'project_name_{i}'].setStringValue_(name)
                self.widgets[f'project_hours_{i}'].setIntValue_(hours)
            else:
                self.widgets[f'project_name_{i}'].setStringValue_("")
                self.widgets[f'project_hours_{i}'].setIntValue_(0)

        # Load project definitions
        projects_config = self.current_prefs.get('projects', {})
        proj_list = list(projects_config.items())

        for i in range(self.PROJECT_DEFINITION_ROWS):
            if i < len(proj_list):
                name, defn = proj_list[i]
                name_popup = self.widgets[f'pd_name_{i}']
                # Add name to popup if missing (e.g. cache was cleared)
                if name_popup.indexOfItemWithTitle_(name) == -1:
                    name_popup.insertItemWithTitle_atIndex_(name, 1)
                name_popup.selectItemWithTitle_(name)
                self.widgets[f'pd_type_{i}'].selectItemWithTitle_(self._defn_to_type_label(defn))
                self.widgets[f'pd_monthly_{i}'].setDoubleValue_(float(defn.get('monthly_amount', 0)))
                self.widgets[f'pd_target_{i}'].setDoubleValue_(float(defn.get('target_hours', 0)))
                self.widgets[f'pd_cap_{i}'].setDoubleValue_(float(defn.get('cap_hours', 0)))
                self.widgets[f'pd_rate_{i}'].setDoubleValue_(float(defn.get('hourly_rate', 0)))
            else:
                self.widgets[f'pd_name_{i}'].selectItemWithTitle_("—")
                self.widgets[f'pd_type_{i}'].selectItemWithTitle_("hourly")
                self.widgets[f'pd_monthly_{i}'].setDoubleValue_(0.0)
                self.widgets[f'pd_target_{i}'].setDoubleValue_(0.0)
                self.widgets[f'pd_cap_{i}'].setDoubleValue_(0.0)
                self.widgets[f'pd_rate_{i}'].setDoubleValue_(0.0)
            self._update_project_row_visibility(i)

    def handleSave_(self, sender):
        """Save button clicked."""
        # Build project_targets dict from 5 name/hours field pairs
        project_targets = {}
        for i in range(self.PROJECT_TARGET_ROWS):
            name = self.widgets[f'project_name_{i}'].stringValue().strip()
            hours = self.widgets[f'project_hours_{i}'].intValue()
            if name:  # Only add if name is not empty
                project_targets[name] = hours

        # Build projects dict from project definition rows
        projects_config = {}
        for i in range(self.PROJECT_DEFINITION_ROWS):
            name = self.widgets[f'pd_name_{i}'].titleOfSelectedItem()
            if not name or name == "—":
                continue
            type_label = self.widgets[f'pd_type_{i}'].titleOfSelectedItem()
            billing_type, hour_tracking = self._type_label_to_defn_fields(type_label)
            defn = {'billing_type': billing_type}
            if billing_type == 'hourly_with_cap':
                defn['hourly_rate'] = self.widgets[f'pd_rate_{i}'].doubleValue()
                defn['cap_hours'] = self.widgets[f'pd_cap_{i}'].doubleValue()
            elif billing_type == 'fixed_monthly':
                defn['monthly_amount'] = self.widgets[f'pd_monthly_{i}'].doubleValue()
                defn['hour_tracking'] = hour_tracking
                if hour_tracking in ('required', 'soft'):
                    defn['target_hours'] = self.widgets[f'pd_target_{i}'].doubleValue()
            # billing_type == 'hourly': no extra fields needed
            projects_config[name] = defn

        # Preserve settings not currently editable in the UI
        new_prefs = self.current_prefs.copy()
        new_prefs.update({
            'cache_ttl_projects': self.widgets['cache_ttl_projects'].intValue(),
            'cache_ttl_today': self.widgets['cache_ttl_today'].intValue(),
            'vacation_days_per_month': self.widgets['vacation_days'].intValue(),
            'project_targets': project_targets,
            'projects': projects_config,
        })

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
            # Keep in-memory state aligned with defaults for fields not shown in UI.
            self.current_prefs = DEFAULT_PREFERENCES.copy()

            # Load defaults into widgets
            self.widgets['cache_ttl_projects'].setIntValue_(DEFAULT_PREFERENCES['cache_ttl_projects'])
            self.widgets['cache_ttl_today'].setIntValue_(DEFAULT_PREFERENCES['cache_ttl_today'])
            self.widgets['vacation_days'].setIntValue_(DEFAULT_PREFERENCES['vacation_days_per_month'])

            # Clear all project target fields
            for i in range(self.PROJECT_TARGET_ROWS):
                self.widgets[f'project_name_{i}'].setStringValue_("")
                self.widgets[f'project_hours_{i}'].setIntValue_(0)

            # Clear all project definition fields
            for i in range(self.PROJECT_DEFINITION_ROWS):
                self.widgets[f'pd_name_{i}'].selectItemWithTitle_("—")
                self.widgets[f'pd_type_{i}'].selectItemWithTitle_("hourly")
                self.widgets[f'pd_monthly_{i}'].setDoubleValue_(0.0)
                self.widgets[f'pd_target_{i}'].setDoubleValue_(0.0)
                self.widgets[f'pd_cap_{i}'].setDoubleValue_(0.0)
                self.widgets[f'pd_rate_{i}'].setDoubleValue_(0.0)
