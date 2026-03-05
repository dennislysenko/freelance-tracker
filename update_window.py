"""Update progress window for Freelance Tracker."""

import threading
import subprocess
from pathlib import Path
from AppKit import (
    NSApp, NSPanel, NSBackingStoreBuffered, NSMakeRect,
    NSTextField, NSProgressIndicator, NSScreen, NSFont, NSButton,
    NSWindowStyleMaskTitled, NSWindowStyleMaskClosable, NSColor,
)

NSProgressIndicatorBarStyle = 0


class UpdateWindowController:
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
        self.title_label = None
        self.status_label = None
        self.progress_bar = None
        self.close_btn = None
        self._running = False

    def show_and_run(self):
        if self._running:
            if self.window:
                self.window.makeKeyAndOrderFront_(None)
                NSApp.activateIgnoringOtherApps_(True)
            return
        if self.window is None:
            self._create_window()
        self._reset_ui()
        self.window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)
        self._running = True
        threading.Thread(target=self._run_update, daemon=True).start()

    def _create_window(self):
        screen_frame = NSScreen.mainScreen().frame()
        w, h = 420, 150
        x = (screen_frame.size.width - w) / 2
        y = (screen_frame.size.height - h) / 2

        self.window = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, w, h),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
            NSBackingStoreBuffered,
            False
        )
        self.window.setTitle_("Freelance Tracker")
        self.window.setHidesOnDeactivate_(False)

        content = self.window.contentView()

        # Heading
        self.title_label = NSTextField.alloc().initWithFrame_(NSMakeRect(20, 108, 380, 24))
        self.title_label.setStringValue_("Updating Freelance Tracker")
        self.title_label.setBezeled_(False)
        self.title_label.setDrawsBackground_(False)
        self.title_label.setEditable_(False)
        self.title_label.setFont_(NSFont.boldSystemFontOfSize_(14))
        content.addSubview_(self.title_label)

        # Step label
        self.status_label = NSTextField.alloc().initWithFrame_(NSMakeRect(20, 84, 380, 18))
        self.status_label.setStringValue_("Starting...")
        self.status_label.setBezeled_(False)
        self.status_label.setDrawsBackground_(False)
        self.status_label.setEditable_(False)
        self.status_label.setFont_(NSFont.systemFontOfSize_(12))
        self.status_label.setTextColor_(NSColor.secondaryLabelColor())
        content.addSubview_(self.status_label)

        # Progress bar
        self.progress_bar = NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(20, 54, 380, 16))
        self.progress_bar.setStyle_(NSProgressIndicatorBarStyle)
        self.progress_bar.setIndeterminate_(False)
        self.progress_bar.setMinValue_(0.0)
        self.progress_bar.setMaxValue_(3.0)
        self.progress_bar.setDoubleValue_(0.0)
        content.addSubview_(self.progress_bar)

        # Close button (shown only on error)
        self.close_btn = NSButton.alloc().initWithFrame_(NSMakeRect(310, 14, 90, 28))
        self.close_btn.setTitle_("Close")
        self.close_btn.setBezelStyle_(1)
        self.close_btn.setTarget_(self)
        self.close_btn.setAction_("handleClose:")
        self.close_btn.setHidden_(True)
        content.addSubview_(self.close_btn)

    def _reset_ui(self):
        self.status_label.setStringValue_("Starting...")
        self.status_label.setTextColor_(NSColor.secondaryLabelColor())
        self.progress_bar.setDoubleValue_(0.0)
        self.close_btn.setHidden_(True)

    def _set_step(self, label, progress):
        self.status_label.setStringValue_(label)
        self.progress_bar.setDoubleValue_(progress)

    def _run_update(self):
        install_dir = Path(__file__).parent
        plist_dest = Path.home() / "Library" / "LaunchAgents" / "com.freelancetracker.menubar.plist"
        venv_pip = install_dir / "venv" / "bin" / "pip"
        requirements = install_dir / "requirements.txt"

        try:
            self._set_step("Pulling latest code...", 0.5)
            result = subprocess.run(
                ["git", "-C", str(install_dir), "pull"],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                self._finish_error(f"git pull failed: {result.stderr.strip()}")
                return

            self._set_step("Updating dependencies...", 1.5)
            result = subprocess.run(
                [str(venv_pip), "install", "-q", "-r", str(requirements)],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                self._finish_error(f"pip install failed: {result.stderr.strip()}")
                return

            self._set_step("Restarting... see you in a moment!", 3.0)
            subprocess.Popen(
                f"sleep 1 && launchctl unload '{plist_dest}' && launchctl load '{plist_dest}'",
                shell=True,
                start_new_session=True
            )

        except Exception as e:
            self._finish_error(str(e))

    def _finish_error(self, message):
        self._running = False
        self.status_label.setTextColor_(NSColor.systemRedColor())
        self._set_step(f"Failed: {message}", self.progress_bar.doubleValue())
        self.close_btn.setHidden_(False)

    def handleClose_(self, sender):
        self._running = False
        self.window.close()
