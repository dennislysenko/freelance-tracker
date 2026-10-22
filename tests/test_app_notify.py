"""The cross-process nudge that keeps the menu bar total current."""

import pytest

import app_notify


@pytest.fixture
def marker(monkeypatch, tmp_path):
    path = tmp_path / "data-changed"
    monkeypatch.setattr(app_notify, "MARKER_FILE", path)
    return path


def test_marker_is_written_with_a_reason(marker):
    app_notify.touch_marker("mcp log_time 42")
    assert "mcp log_time 42" in marker.read_text()
    assert app_notify.marker_mtime() > 0


def test_missing_marker_reads_as_never(marker):
    assert app_notify.marker_mtime() == 0.0


def test_marker_advances_on_each_change(marker):
    app_notify.touch_marker("first")
    first = app_notify.marker_mtime()
    import os
    os.utime(marker, (first - 10, first - 10))
    app_notify.touch_marker("second")
    assert app_notify.marker_mtime() > first - 10


def test_touch_survives_an_unwritable_path(monkeypatch, tmp_path):
    monkeypatch.setattr(app_notify, "MARKER_FILE", tmp_path / "nope" / "x" / "marker")
    monkeypatch.setattr(
        app_notify.MARKER_FILE.parent.__class__, "mkdir",
        lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")),
    )
    app_notify.touch_marker("boom")  # must not raise


def test_notify_posts_and_marks(marker, monkeypatch):
    posted = []
    monkeypatch.setattr(app_notify, "post_notification", lambda reason="": posted.append(reason))
    app_notify.notify_data_changed("mcp delete_entry 7")
    assert posted == ["mcp delete_entry 7"]
    assert "delete_entry 7" in marker.read_text()


def test_post_without_pyobjc_is_not_fatal(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_foundation(name, *args, **kwargs):
        if name == "Foundation":
            raise ImportError("no PyObjC here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_foundation)
    assert app_notify.post_notification("x") is False


def test_posts_for_real_on_this_machine():
    """PyObjC can actually post on this machine.

    This is the one test that touches the real notification centre, so a
    regression in the posting call is caught. A running menu bar app will
    re-read its cache once as a result, which costs no Toggl calls.
    """
    assert app_notify.post_notification("test suite") is True
    assert app_notify.NOTIFICATION_NAME == "com.freelancetracker.dataChanged"
