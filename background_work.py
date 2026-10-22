"""Run slow work off the main thread and deliver the result back onto it.

This app is otherwise single-threaded: rumps timers, the bridge message
handler, and every network call all run on the main thread, so a slow request
freezes the menu bar and the popover cannot repaint. That is tolerable for a
fast Stripe call and not tolerable for a 1-3 second OpenAI call.

AppKit and WKWebView are main-thread-only, so a worker must never touch the
popover or call evaluateJavaScript directly. `run_in_background` enforces the
split: `work` runs on a worker thread, `on_done` is delivered back on the main
thread via NSOperationQueue.

Generations
-----------
Callers that render into the web view should pair this with a monotonically
increasing token: the document is reloaded by `loadHTML_` on every dashboard
refresh, and a reply arriving after a newer request was issued must not
overwrite it. `Generation` provides that counter.
"""

from __future__ import annotations

import threading
import traceback

try:
    import objc

    NSOperationQueue = objc.lookUpClass("NSOperationQueue")
except Exception:  # pragma: no cover - only when PyObjC is unavailable
    NSOperationQueue = None


def _debug(msg):
    try:
        from datetime import datetime as _dt

        with open("/tmp/freelance_dashboard_debug.log", "a") as f:
            f.write(f"{_dt.now().isoformat()} [bg] {msg}\n")
    except Exception:
        pass


def on_main_thread(fn):
    """Schedule `fn` on the main thread.

    Falls back to calling inline when PyObjC is unavailable (tests, CLI), which
    keeps this module importable outside the app.
    """
    if NSOperationQueue is None:
        fn()
        return
    try:
        NSOperationQueue.mainQueue().addOperationWithBlock_(fn)
    except Exception as exc:  # pragma: no cover - defensive
        _debug(f"main-thread dispatch failed, running inline: {exc}")
        fn()


def run_in_background(work, on_done, on_error=None, name="bg-work", dispatch=None):
    """
    Run `work()` on a worker thread; deliver its result to `on_done` on the
    main thread.

    An exception in `work` goes to `on_error` (also on the main thread) rather
    than killing the thread silently. `on_done` and `on_error` are the only
    places allowed to touch AppKit.

    `dispatch` overrides how completions are delivered. It exists for tests: a
    process that has imported PyObjC but runs no main run loop will queue
    main-thread blocks that never execute, so tests pass a direct dispatcher
    rather than depending on import order.
    """
    deliver = dispatch or on_main_thread

    def _runner():
        try:
            result = work()
        except Exception as exc:
            _debug(f"{name} failed: {exc}\n{traceback.format_exc()}")
            if on_error is not None:
                deliver(lambda: on_error(exc))
            return
        deliver(lambda: on_done(result))

    thread = threading.Thread(target=_runner, name=name, daemon=True)
    thread.start()
    return thread


class Generation:
    """Monotonic counter for discarding superseded async replies.

    Submitting a second command before the first returns must not let the
    slower, older reply land on top of the newer one.
    """

    def __init__(self):
        self._value = 0
        self._lock = threading.Lock()

    def next(self):
        with self._lock:
            self._value += 1
            return self._value

    @property
    def current(self):
        with self._lock:
            return self._value

    def is_current(self, token):
        with self._lock:
            return token == self._value
