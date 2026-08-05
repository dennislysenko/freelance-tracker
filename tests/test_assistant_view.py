"""Tests for the assistant view rendering."""

import base64
import unittest

import nl_time
from assistant import AssistantSession
from assistant_view import (
    generate_assistant_html,
    generate_assistant_js,
    render_launcher,
)


PROJECTS = {"101": {"name": "Acme"}}


def _resolved(count=1, description=None):
    entries = [
        {
            "project": "Acme",
            "date_kind": "absolute",
            "date_absolute": f"2026-08-{3 + i:02d}",
            "date_days_ago": None,
            "date_weekday": None,
            "date_direction": None,
            "start_time": "09:00",
            "duration_minutes": 60,
            "description": description,
            "billable": True,
        }
        for i in range(count)
    ]
    return nl_time.resolve_entries(entries, nl_time.project_name_map(PROJECTS))


class TestLauncher(unittest.TestCase):
    def test_renders_when_a_key_is_configured(self):
        self.assertIn("assistantLauncher", render_launcher(True))

    def test_absent_without_a_key(self):
        # No key is the feature's off switch; no preference needed.
        self.assertEqual(render_launcher(False), "")


class TestTranscriptRendering(unittest.TestCase):
    def test_empty_state_shows_examples(self):
        html = generate_assistant_html(AssistantSession())
        self.assertIn("Log time or ask", html)
        self.assertIn("no note", html)

    def test_user_turn_is_escaped(self):
        s = AssistantSession()
        s.add_user_turn('<script>alert("x")</script>')
        html = generate_assistant_html(s)
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)

    def test_error_turn_uses_error_styling(self):
        s = AssistantSession()
        s.add_error_turn("This key has no available credit.")
        html = generate_assistant_html(s)
        self.assertIn("assistant-bubble error", html)
        self.assertIn("no available credit", html)

    def test_pending_shows_thinking(self):
        html = generate_assistant_html(AssistantSession(), pending=True)
        self.assertIn("Thinking", html)
        # Input is disabled so a second command cannot race the first.
        self.assertIn("disabled", html)


class TestProposalCard(unittest.TestCase):
    def _session_with(self, count=2, collisions=()):
        s = AssistantSession()
        s.accept({
            "kind": "proposal",
            "entries": _resolved(count),
            "collisions": list(collisions),
        })
        return s

    def test_renders_a_row_per_entry(self):
        html = generate_assistant_html(self._session_with(2))
        self.assertEqual(html.count('class="prop-row'), 2)
        self.assertIn("Proposed 2 entries", html)

    def test_singular_wording_for_one_entry(self):
        html = generate_assistant_html(self._session_with(1))
        self.assertIn("Proposed 1 entry", html)

    def test_clean_rows_are_checked(self):
        html = generate_assistant_html(self._session_with(1))
        self.assertIn("checked", html)

    def test_colliding_row_is_unchecked_and_warned(self):
        html = generate_assistant_html(self._session_with(2, collisions=[1]))
        self.assertIn("Overlaps time already logged", html)
        self.assertIn("prop-row collide", html)
        self.assertIn("1 overlap existing time", html)
        # One checked box (the clean row), not two.
        self.assertEqual(html.count(" checked>"), 1)

    def test_shows_time_range_project_and_duration(self):
        html = generate_assistant_html(self._session_with(1))
        self.assertIn("9:00am", html)
        self.assertIn("10:00am", html)
        self.assertIn("Acme", html)
        self.assertIn("1.00h", html)

    def test_description_is_shown_and_escaped(self):
        s = AssistantSession()
        s.accept({
            "kind": "proposal",
            "entries": _resolved(1, description='a & <b>'),
            "collisions": [],
        })
        html = generate_assistant_html(s)
        self.assertIn("a &amp; &lt;b&gt;", html)

    def test_applied_card_reports_result_and_drops_actions(self):
        s = AssistantSession(writer=lambda **kw: None)
        p = s.accept({"kind": "proposal", "entries": _resolved(2), "collisions": []})
        s.apply_proposal(p.id, [0, 1])
        html = generate_assistant_html(s)
        self.assertIn("Logged 2 entries to Toggl", html)
        self.assertNotIn(f"assistantApply('{p.id}')", html)


class TestBridgeEncoding(unittest.TestCase):
    def test_js_base64_encodes_the_utterance(self):
        js = generate_assistant_js()
        # The handler splits on ':' and spaces, so raw text cannot be sent.
        self.assertIn("assistantEncode", js)
        self.assertIn("btoa", js)
        self.assertIn("encodeURIComponent", js)

    def test_python_decodes_what_the_js_encoder_produces(self):
        # Values captured from the JS encoder in a real browser.
        for encoded, expected in [
            ("bm90ZTogc3ByaW50IHBsYW5uaW5nLCAyOjMwIHN0YW5kdXA=",
             "note: sprint planning, 2:30 standup"),
            ("Y2Fmw6kg4piVIDJoIG9uIFRyaVNob3Q=", "café ☕ 2h on TriShot"),
        ]:
            self.assertEqual(base64.b64decode(encoded).decode("utf-8"), expected)


if __name__ == "__main__":
    unittest.main()
