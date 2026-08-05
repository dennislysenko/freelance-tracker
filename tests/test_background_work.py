"""Tests for the background-work helper.

PyObjC is unavailable in the test process, so `on_main_thread` runs inline —
which is exactly the fallback these assert.
"""

import threading
import unittest

from background_work import Generation, run_in_background

# Deliver completions inline. With PyObjC imported (which happens as soon as
# any sibling test imports dashboard_panel) the real dispatcher queues onto a
# main run loop that a test process never runs, so completions would hang.
INLINE = lambda fn: fn()


class TestRunInBackground(unittest.TestCase):
    def test_result_is_delivered_to_on_done(self):
        done = threading.Event()
        got = []
        run_in_background(
            lambda: 41 + 1, lambda r: (got.append(r), done.set()), dispatch=INLINE
        )
        self.assertTrue(done.wait(5))
        self.assertEqual(got, [42])

    def test_work_runs_off_the_calling_thread(self):
        done = threading.Event()
        seen = {}
        run_in_background(
            lambda: seen.setdefault("worker", threading.current_thread().name),
            lambda _: done.set(),
            dispatch=INLINE,
        )
        self.assertTrue(done.wait(5))
        self.assertNotEqual(seen["worker"], threading.current_thread().name)

    def test_exception_goes_to_on_error_not_on_done(self):
        done = threading.Event()
        errors, results = [], []

        def boom():
            raise RuntimeError("nope")

        run_in_background(
            boom,
            lambda r: (results.append(r), done.set()),
            lambda e: (errors.append(e), done.set()),
            dispatch=INLINE,
        )
        self.assertTrue(done.wait(5))
        self.assertEqual(results, [])
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], RuntimeError)

    def test_failure_without_error_handler_does_not_propagate(self):
        # A worker blowing up must not take the app down.
        thread = run_in_background(lambda: 1 / 0, lambda _: None, dispatch=INLINE)
        thread.join(5)
        self.assertFalse(thread.is_alive())


class TestGeneration(unittest.TestCase):
    def test_next_increments_and_tracks_current(self):
        gen = Generation()
        first = gen.next()
        second = gen.next()
        self.assertEqual((first, second), (1, 2))
        self.assertEqual(gen.current, 2)

    def test_only_the_newest_token_is_current(self):
        gen = Generation()
        stale = gen.next()
        fresh = gen.next()
        # A slow reply from the superseded request must be discardable.
        self.assertFalse(gen.is_current(stale))
        self.assertTrue(gen.is_current(fresh))

    def test_concurrent_next_hands_out_unique_tokens(self):
        gen = Generation()
        tokens, lock = [], threading.Lock()

        def grab():
            token = gen.next()
            with lock:
                tokens.append(token)

        threads = [threading.Thread(target=grab) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        self.assertEqual(len(set(tokens)), 50)


if __name__ == "__main__":
    unittest.main()
