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
