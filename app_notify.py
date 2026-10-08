"""Tell a running menu bar app that its cached data changed under it.

The MCP server runs in its own process, spawned by whichever agent host the
user is talking to. When it writes a time entry it patches the shared day
shard, but the menu bar process has no reason to re-read anything, so the
💰 total stays stale until its next 30-minute refresh.

Two signals, because they fail in different ways:

* A **distributed notification**, which the app picks up immediately. This is
  the one that matters; posting it is cheap and needs no reply.
* A **marker file**, which the app also polls once a minute. It covers the
  case where the notification is not delivered (PyObjC unavailable in the
  writing process, or the notification dropped), so the worst case is a
  minute of staleness rather than half an hour.

Neither costs a Toggl call: the writer has already patched the cache, so the
app's re-read is served entirely from disk.
"""

from __future__ import annotations

import os
from datetime import datetime

from preferences import CACHE_DIR

NOTIFICATION_NAME = "com.freelancetracker.dataChanged"
MARKER_FILE = CACHE_DIR / "data-changed"


def touch_marker(reason: str = "") -> None:
    """Record that cached data changed. Never raises."""
    try:
        MARKER_FILE.parent.mkdir(parents=True, exist_ok=True)
        MARKER_FILE.write_text(
            f"{datetime.now().isoformat()} {reason}\n", encoding="utf-8"
        )
    except OSError:
        pass


def marker_mtime() -> float:
    """Last time anything announced a change, or 0.0 if never."""
    try:
        return MARKER_FILE.stat().st_mtime
    except OSError:
        return 0.0


def post_notification(reason: str = "") -> bool:
    """Post the distributed notification. Returns whether it went out.

    Imported lazily and guarded: a writer that cannot load PyObjC should still
    complete its write, just without the instant refresh.
    """
    try:
        from Foundation import NSDistributedNotificationCenter
    except Exception:
        return False
    try:
        NSDistributedNotificationCenter.defaultCenter().postNotificationName_object_userInfo_deliverImmediately_(
            NOTIFICATION_NAME, None, {"reason": str(reason), "pid": str(os.getpid())}, True
        )
        return True
    except Exception:
        return False


def notify_data_changed(reason: str = "") -> None:
    """Announce a cache change to a running menu bar app, if there is one."""
    touch_marker(reason)
    post_notification(reason)
