"""Modal NSAlert with two NSDatePickers for picking a date range."""

from datetime import date, datetime

from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSDatePicker,
    NSTextField,
    NSView,
    NSMakeRect,
)
from Foundation import NSDate, NSCalendar, NSDateComponents


def _date_to_nsdate(d):
    components = NSDateComponents.alloc().init()
    components.setYear_(d.year)
    components.setMonth_(d.month)
    components.setDay_(d.day)
    components.setHour_(12)
    components.setMinute_(0)
    components.setSecond_(0)
    return NSCalendar.currentCalendar().dateFromComponents_(components)


def _nsdate_to_date(nsdate):
    components = NSCalendar.currentCalendar().components_fromDate_(
        (1 << 2) | (1 << 3) | (1 << 4),  # Year | Month | Day
        nsdate,
    )
    return date(components.year(), components.month(), components.day())


def prompt_date_range(default_start, default_end, message_text="Choose date range"):
    """Show a modal dialog with two date pickers. Returns (start, end) or None.

    Both inputs default to the provided dates. Returns (start, end) on OK,
    None on cancel. Validates start <= end (silently swaps if reversed).
    """
    alert = NSAlert.alloc().init()
    alert.setMessageText_(message_text)
    alert.setInformativeText_("Pick the first and last day to include.")
    alert.addButtonWithTitle_("Export")
    alert.addButtonWithTitle_("Cancel")

    accessory = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 320, 70))

    start_label = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 40, 80, 22))
    start_label.setStringValue_("Start:")
    start_label.setBezeled_(False)
    start_label.setDrawsBackground_(False)
    start_label.setEditable_(False)
    start_label.setSelectable_(False)
    accessory.addSubview_(start_label)

    start_picker = NSDatePicker.alloc().initWithFrame_(NSMakeRect(80, 38, 230, 26))
    start_picker.setDatePickerStyle_(1)  # NSDatePickerStyleTextFieldAndStepper
    start_picker.setDatePickerElements_(1)  # NSDatePickerElementFlagYearMonthDay
    start_picker.setDateValue_(_date_to_nsdate(default_start))
    accessory.addSubview_(start_picker)

    end_label = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 8, 80, 22))
    end_label.setStringValue_("End:")
    end_label.setBezeled_(False)
    end_label.setDrawsBackground_(False)
    end_label.setEditable_(False)
    end_label.setSelectable_(False)
    accessory.addSubview_(end_label)

    end_picker = NSDatePicker.alloc().initWithFrame_(NSMakeRect(80, 6, 230, 26))
    end_picker.setDatePickerStyle_(1)
    end_picker.setDatePickerElements_(1)
    end_picker.setDateValue_(_date_to_nsdate(default_end))
    accessory.addSubview_(end_picker)

    alert.setAccessoryView_(accessory)

    response = alert.runModal()
    if response != NSAlertFirstButtonReturn:
        return None

    start_d = _nsdate_to_date(start_picker.dateValue())
    end_d = _nsdate_to_date(end_picker.dateValue())
    if start_d > end_d:
        start_d, end_d = end_d, start_d
    return start_d, end_d
