"""Tests for the assistant session: transcript, proposals, and the write path."""

import unittest
from datetime import date, datetime, timedelta, timezone

import nl_time
from assistant import MAX_TURNS, AssistantSession


PROJECTS = {"101": {"name": "Acme"}, "202": {"name": "TriShot"}}


def _parsed_entry(**overrides):
    base = {
        "project": "Acme",
        "date_kind": "absolute",
        "date_absolute": "2026-08-03",
        "date_days_ago": None,
        "date_weekday": None,
        "date_direction": None,
        "start_time": "09:00",
        "duration_minutes": 60,
        "description": None,
        "billable": True,
    }
    base.update(overrides)
    return base


def _resolved(count=1):
    entries = [
        _parsed_entry(date_absolute=f"2026-08-{3 + i:02d}") for i in range(count)
    ]
    return nl_time.resolve_entries(entries, nl_time.project_name_map(PROJECTS))


class TestTranscript(unittest.TestCase):
    def test_turns_accumulate_in_order(self):
        s = AssistantSession()
        s.add_user_turn("hello")
        s.add_note_turn("hi")
        self.assertEqual([t.role for t in s.turns], ["user", "assistant"])

    def test_transcript_is_capped(self):
        s = AssistantSession()
        for i in range(MAX_TURNS + 10):
            s.add_user_turn(f"m{i}")
        self.assertEqual(len(s.turns), MAX_TURNS)
        # Oldest dropped, newest kept.
        self.assertEqual(s.turns[-1].text, f"m{MAX_TURNS + 9}")

    def test_dropping_turns_releases_their_proposals(self):
        s = AssistantSession()
        proposal = s.accept({"kind": "proposal", "entries": _resolved(), "collisions": []})
        for i in range(MAX_TURNS + 5):
            s.add_user_turn(f"m{i}")
        # The proposal's turn aged out, so applying it must not still work.
        self.assertIsNone(s.get_proposal(proposal.id))

    def test_clear_resets_everything(self):
        s = AssistantSession()
        p = s.accept({"kind": "proposal", "entries": _resolved(), "collisions": []})
        s.clear()
        self.assertEqual(s.turns, [])
        self.assertIsNone(s.get_proposal(p.id))


class TestProposalSelection(unittest.TestCase):
    def test_clean_rows_start_checked(self):
        s = AssistantSession()
        p = s.accept({"kind": "proposal", "entries": _resolved(2), "collisions": []})
        self.assertEqual(p.default_selection(), [0, 1])

    def test_colliding_rows_start_unchecked(self):
        s = AssistantSession()
        p = s.accept({"kind": "proposal", "entries": _resolved(2), "collisions": [1]})
        # Re-running "the past 2 days" must not silently double-log.
        self.assertEqual(p.default_selection(), [0])

    def test_proposal_ids_are_unique(self):
        s = AssistantSession()
        a = s.accept({"kind": "proposal", "entries": _resolved(), "collisions": []})
        b = s.accept({"kind": "proposal", "entries": _resolved(), "collisions": []})
        self.assertNotEqual(a.id, b.id)


class TestApply(unittest.TestCase):
    def setUp(self):
        self.written = []
        self.session = AssistantSession(writer=self._writer)

    def _writer(self, **kwargs):
        self.written.append(kwargs)

    def _proposal(self, count=2, collisions=()):
        return self.session.accept(
            {"kind": "proposal", "entries": _resolved(count), "collisions": list(collisions)}
        )

    def test_writes_only_selected_rows(self):
        p = self._proposal(2)
        count, err = self.session.apply_proposal(p.id, [0])
        self.assertIsNone(err)
        self.assertEqual(count, 1)
        self.assertEqual(len(self.written), 1)

    def test_passes_create_time_entry_kwargs(self):
        p = self._proposal(1)
        self.session.apply_proposal(p.id, [0])
        call = self.written[0]
        self.assertEqual(set(call), {
            "start", "duration_seconds", "description", "project_id", "billable"
        })
        self.assertIsNotNone(call["start"].tzinfo)
        self.assertEqual(call["duration_seconds"], 3600)

    def test_double_apply_is_refused(self):
        p = self._proposal(1)
        self.session.apply_proposal(p.id, [0])
        count, err = self.session.apply_proposal(p.id, [0])
        self.assertEqual(count, 0)
        self.assertIn("already", err)
        self.assertEqual(len(self.written), 1)

    def test_unknown_proposal_is_refused(self):
        count, err = self.session.apply_proposal("nope", [0])
        self.assertEqual((count, bool(err)), (0, True))

    def test_empty_selection_writes_nothing(self):
        p = self._proposal(2)
        count, err = self.session.apply_proposal(p.id, [])
        self.assertEqual(count, 0)
        self.assertEqual(self.written, [])

    def test_out_of_range_indices_are_ignored(self):
        p = self._proposal(1)
        count, err = self.session.apply_proposal(p.id, [0, 5, -3])
        self.assertEqual(count, 1)
        self.assertEqual(len(self.written), 1)

    def test_partial_failure_reports_what_landed(self):
        calls = {"n": 0}

        def flaky(**kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("Toggl said no")

        session = AssistantSession(writer=flaky)
        p = session.accept(
            {"kind": "proposal", "entries": _resolved(3), "collisions": []}
        )
        count, err = session.apply_proposal(p.id, [0, 1, 2])
        # The first entry really is in Toggl; saying "failed" alone would lie.
        self.assertEqual(count, 1)
        self.assertIn("Logged 1 of 3", err)

    def test_failed_apply_can_be_retried(self):
        def boom(**kwargs):
            raise RuntimeError("nope")

        session = AssistantSession(writer=boom)
        p = session.accept({"kind": "proposal", "entries": _resolved(1), "collisions": []})
        session.apply_proposal(p.id, [0])
        # Not marked applied, so the user can try again after fixing the cause.
        count, err = session.apply_proposal(p.id, [0])
        self.assertNotIn("already", err or "")

    def test_applied_days_reports_touched_days(self):
        p = self._proposal(2)
        self.assertEqual(
            self.session.applied_days(p.id), [date(2026, 8, 3), date(2026, 8, 4)]
        )


class TestResolve(unittest.TestCase):
    def test_collision_lookup_failure_does_not_block(self):
        def angry(*args, **kwargs):
            raise RuntimeError("cache exploded")

        session = AssistantSession(
            projects_provider=lambda: PROJECTS, entries_provider=angry
        )
        # Should degrade to "no known collisions", not raise.
        self.assertEqual(session._existing_entries_for(_resolved(1)), [])

    def test_no_projects_is_a_config_error(self):
        session = AssistantSession(projects_provider=lambda: {})
        with self.assertRaises(nl_time.NLConfigError):
            session.resolve("log an hour")

    def test_accept_note_appends_plain_turn(self):
        s = AssistantSession()
        result = s.accept({"kind": "note", "message": "I could not tell what to log."})
        self.assertIsNone(result)
        self.assertEqual(s.turns[-1].text, "I could not tell what to log.")


if __name__ == "__main__":
    unittest.main()


class TestConversationHistory(unittest.TestCase):
    """Follow-ups must reach the model with the earlier turns, or "round to
    the half hour mark" parses as a command with no subject."""

    def _session_with_proposal(self):
        session = AssistantSession(projects_provider=lambda: {"1": {"name": "Acme"}})
        session.add_user_turn("log an hour of Acme, past hour")
        proposal = session.accept({
            "kind": "proposal",
            "entries": _resolved(1),
            "collisions": [],
            "message": "Interpreted 'past hour' as 12:34-13:34.",
        })
        return session, proposal

    def test_history_spells_out_proposal_with_absolute_date(self):
        session, proposal = self._session_with_proposal()
        history = session.history_for_model()
        self.assertEqual([m["role"] for m in history], ["user", "assistant"])
        self.assertEqual(history[0]["content"], "log an hour of Acme, past hour")
        body = history[1]["content"]
        item = proposal.entries[0]
        self.assertIn("Interpreted 'past hour'", body)
        self.assertIn(f"date {item['day'].isoformat()}", body)
        self.assertIn(f"start_time {item['start'].strftime('%H:%M')}", body)
        self.assertIn(f"duration_minutes {item['duration_seconds'] // 60}", body)
        self.assertIn("project 'Acme'", body)
        self.assertIn("not yet logged", body)

    def test_applied_proposal_is_marked_in_history(self):
        session, proposal = self._session_with_proposal()
        proposal.applied = True
        proposal.applied_count = 1
        self.assertIn("Applied (1 logged", session.history_for_model()[1]["content"])

    def test_history_is_bounded(self):
        session = AssistantSession()
        for i in range(30):
            session.add_user_turn(f"u{i}")
        self.assertEqual(len(session.history_for_model(limit=4)), 4)
        self.assertEqual(session.history_for_model(limit=4)[-1]["content"], "u29")

    def test_resolve_passes_history_to_interpret(self):
        session, _ = self._session_with_proposal()
        captured = {}

        def fake_interpret(utterance, names, history=None, **kw):
            captured["utterance"] = utterance
            captured["history"] = history
            return {"intent": "unclear", "message": "stub"}

        original = nl_time.interpret
        nl_time.interpret = fake_interpret
        try:
            session.resolve("round to half hour mark")
        finally:
            nl_time.interpret = original
        self.assertEqual(captured["utterance"], "round to half hour mark")
        self.assertEqual(len(captured["history"]), 2)
        self.assertIn("Proposed entries", captured["history"][1]["content"])


class TestQuestions(unittest.TestCase):
    """A question must be answered from the cache, not just acknowledged."""

    def _session(self, entries):
        projects = {"1": {"name": "Acme"}, "2": {"name": "Globex"}}
        calls = []

        def provider(start, end):
            calls.append((start, end))
            return entries

        session = AssistantSession(projects_provider=lambda: projects,
                                   entries_provider=provider)
        session._calls = calls
        return session

    def _entry(self, pid, start_iso, minutes, desc=""):
        return {"project_id": pid, "start": start_iso, "duration": minutes * 60,
                "description": desc}

    def _ask(self, session, query):
        original = nl_time.interpret
        nl_time.interpret = lambda *a, **k: {"intent": "question", "query": query,
                                              "message": "Listing."}
        try:
            return session.resolve("what did I log")
        finally:
            nl_time.interpret = original

    def test_single_day_lists_entries_with_totals(self):
        session = self._session([
            self._entry(1, "2026-09-15T13:00:00Z", 60, "MCP fixes"),
            self._entry(2, "2026-09-15T15:30:00Z", 30),
            self._entry(1, "2026-09-15T16:00:00Z", -1),  # running timer
        ])
        result = self._ask(session, {"project": None, "start_date": "2026-09-15",
                                     "end_date": "2026-09-15"})
        self.assertEqual(result["kind"], "answer")
        msg = result["message"]
        self.assertIn("Tue Sep 15: 1.50h total", msg)
        self.assertIn("Acme: 1.00h", msg)
        self.assertIn("Globex: 0.50h", msg)
        self.assertIn("MCP fixes", msg)
        self.assertEqual(session._calls, [(date(2026, 9, 15), date(2026, 9, 15))])

    def test_project_filter_and_empty_result(self):
        session = self._session([self._entry(2, "2026-09-15T15:30:00Z", 30)])
        result = self._ask(session, {"project": "Acme", "start_date": "2026-09-15",
                                     "end_date": "2026-09-15"})
        self.assertEqual(result["message"], "No Acme time logged for Tue Sep 15.")

    def test_long_range_gives_day_totals_only(self):
        entries = [self._entry(1, f"2026-09-{d:02d}T13:00:00Z", 60, "note")
                   for d in range(1, 15)]
        session = self._session(entries)
        result = self._ask(session, {"project": None, "start_date": "2026-09-01",
                                     "end_date": "2026-09-14"})
        msg = result["message"]
        self.assertIn("14.00h total", msg)
        self.assertIn("Mon Sep 14: 1.00h", msg)
        self.assertNotIn("note", msg)

    def test_bad_query_is_reported_not_raised(self):
        session = self._session([])
        result = self._ask(session, {"project": None, "start_date": "??", "end_date": "??"})
        self.assertIn("could not look that up", result["message"])
