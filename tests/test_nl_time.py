"""Tests for natural-language time logging (pure logic; no network calls)."""

import unittest
from datetime import date as _date, datetime, timedelta, timezone

import nl_time


PROJECTS = {"101": {"name": "Randonautica - Retainer"}, "202": {"name": "Acme"}}


class TestProjectNameMap(unittest.TestCase):
    def test_inverts_id_keyed_projects(self):
        self.assertEqual(
            nl_time.project_name_map(PROJECTS),
            {"Randonautica - Retainer": 101, "Acme": 202},
        )

    def test_tolerates_empty_and_nameless(self):
        self.assertEqual(nl_time.project_name_map(None), {})
        self.assertEqual(nl_time.project_name_map({"1": {}}), {})


class TestResolveEntries(unittest.TestCase):
    def setUp(self):
        self.map = nl_time.project_name_map(PROJECTS)

    def _entry(self, **overrides):
        base = {
            "project": "Randonautica - Retainer",
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

    def test_produces_timezone_aware_start(self):
        resolved = nl_time.resolve_entries([self._entry()], self.map)
        self.assertEqual(len(resolved), 1)
        start = resolved[0]["start"]
        # create_time_entry rejects naive datetimes, so this must be aware.
        self.assertIsNotNone(start.tzinfo)
        self.assertIsNotNone(start.tzinfo.utcoffset(start))
        # Local wall clock must be preserved, not shifted.
        self.assertEqual((start.hour, start.minute), (9, 0))

    def test_maps_fields_onto_create_time_entry_kwargs(self):
        resolved = nl_time.resolve_entries(
            [self._entry(duration_minutes=90, description="sprint", billable=False)],
            self.map,
        )[0]
        self.assertEqual(resolved["duration_seconds"], 5400)
        self.assertEqual(resolved["description"], "sprint")
        self.assertEqual(resolved["project_id"], 101)
        self.assertFalse(resolved["billable"])

    def test_null_description_becomes_empty_string(self):
        resolved = nl_time.resolve_entries([self._entry(description=None)], self.map)
        self.assertEqual(resolved[0]["description"], "")

    def test_unknown_project_is_rejected(self):
        with self.assertRaises(ValueError):
            nl_time.resolve_entries([self._entry(project="Nope")], self.map)

    def test_zero_duration_is_rejected(self):
        with self.assertRaises(ValueError):
            nl_time.resolve_entries([self._entry(duration_minutes=0)], self.map)

    def test_malformed_date_is_rejected(self):
        with self.assertRaises(ValueError):
            nl_time.resolve_entries([self._entry(date_absolute="the 3rd")], self.map)

    def test_multi_day_command_resolves_each_day(self):
        resolved = nl_time.resolve_entries(
            [
                self._entry(date_absolute="2026-08-03"),
                self._entry(date_absolute="2026-08-04"),
            ],
            self.map,
        )
        self.assertEqual([item["day"].day for item in resolved], [3, 4])


class TestResolveDay(unittest.TestCase):
    """Date arithmetic lives in Python precisely because the model gets it
    wrong: asked for 'the most recent Friday' it will answer 2026-08-01 (a
    Saturday) and then correctly label that date Saturday, so cross-checking
    the model against itself cannot catch it."""

    # A Wednesday.
    TODAY = _date(2026, 8, 5)

    def _resolve(self, **item):
        return nl_time.resolve_day(item, today=self.TODAY)

    def test_absolute_date(self):
        self.assertEqual(
            self._resolve(date_kind="absolute", date_absolute="2026-07-31"),
            _date(2026, 7, 31),
        )

    def test_days_ago_zero_is_today(self):
        self.assertEqual(self._resolve(date_kind="days_ago", date_days_ago=0), self.TODAY)

    def test_days_ago_counts_back(self):
        self.assertEqual(
            self._resolve(date_kind="days_ago", date_days_ago=2), _date(2026, 8, 3)
        )

    def test_past_weekday_lands_on_the_real_friday(self):
        # The exact case that was silently producing a Saturday.
        resolved = self._resolve(
            date_kind="weekday", date_weekday="Friday", date_direction="past"
        )
        self.assertEqual(resolved, _date(2026, 7, 31))
        self.assertEqual(resolved.strftime("%A"), "Friday")

    def test_every_past_weekday_resolves_to_that_weekday(self):
        for name in nl_time.WEEKDAYS:
            resolved = self._resolve(
                date_kind="weekday", date_weekday=name, date_direction="past"
            )
            self.assertEqual(resolved.strftime("%A"), name)
            self.assertLessEqual(resolved, self.TODAY)

    def test_past_weekday_matching_today_is_today(self):
        self.assertEqual(
            self._resolve(
                date_kind="weekday", date_weekday="Wednesday", date_direction="past"
            ),
            self.TODAY,
        )

    def test_future_weekday_looks_forward(self):
        self.assertEqual(
            self._resolve(
                date_kind="weekday", date_weekday="Friday", date_direction="future"
            ),
            _date(2026, 8, 7),
        )

    def test_future_weekday_matching_today_skips_a_week(self):
        # "next Wednesday" said on a Wednesday means the one coming.
        self.assertEqual(
            self._resolve(
                date_kind="weekday", date_weekday="Wednesday", date_direction="future"
            ),
            _date(2026, 8, 12),
        )

    def test_missing_pieces_are_rejected(self):
        for bad in (
            {"date_kind": "absolute", "date_absolute": None},
            {"date_kind": "days_ago", "date_days_ago": None},
            {"date_kind": "days_ago", "date_days_ago": -1},
            {"date_kind": "weekday", "date_weekday": None},
            {"date_kind": "weekday", "date_weekday": "Caturday"},
            {"date_kind": "nonsense"},
            {},
        ):
            with self.assertRaises(ValueError):
                self._resolve(**bad)


class TestFindCollisions(unittest.TestCase):
    def setUp(self):
        self.map = nl_time.project_name_map(PROJECTS)
        self.resolved = nl_time.resolve_entries(
            [
                {
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
            ],
            self.map,
        )

    def _existing(self, start, duration):
        return {"start": start.astimezone(timezone.utc).isoformat(), "duration": duration}

    def test_no_collision_when_nothing_logged(self):
        self.assertEqual(nl_time.find_collisions(self.resolved, []), [])

    def test_detects_exact_double_log(self):
        existing = [self._existing(self.resolved[0]["start"], 3600)]
        self.assertEqual(nl_time.find_collisions(self.resolved, existing), [0])

    def test_detects_partial_overlap(self):
        overlapping = self.resolved[0]["start"] + timedelta(minutes=30)
        self.assertEqual(
            nl_time.find_collisions(self.resolved, [self._existing(overlapping, 3600)]), [0]
        )

    def test_adjacent_entry_is_not_a_collision(self):
        adjacent = self.resolved[0]["start"] + timedelta(minutes=60)
        self.assertEqual(
            nl_time.find_collisions(self.resolved, [self._existing(adjacent, 3600)]), []
        )

    def test_running_entry_negative_duration_is_ignored(self):
        existing = [self._existing(self.resolved[0]["start"], -1)]
        self.assertEqual(nl_time.find_collisions(self.resolved, existing), [])

    def test_malformed_existing_entries_are_skipped(self):
        existing = [{"start": "not-a-date", "duration": 3600}, {"duration": 3600}]
        self.assertEqual(nl_time.find_collisions(self.resolved, existing), [])


class TestSchema(unittest.TestCase):
    def test_projects_are_an_enum_so_the_model_cannot_invent_one(self):
        schema = nl_time._schema(["Acme", "Randonautica - Retainer"])
        entry_props = schema["schema"]["properties"]["entries"]["items"]["properties"]
        self.assertEqual(entry_props["project"]["enum"], ["Acme", "Randonautica - Retainer"])

    def test_strict_mode_requires_every_property(self):
        schema = nl_time._schema(["Acme"])["schema"]
        self.assertTrue(schema["additionalProperties"] is False)
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        item = schema["properties"]["entries"]["items"]
        self.assertEqual(set(item["required"]), set(item["properties"]))

    def test_query_project_allows_null_for_all_projects(self):
        schema = nl_time._schema(["Acme"])["schema"]
        self.assertIn(None, schema["properties"]["query"]["properties"]["project"]["enum"])


class TestResponsesPayloadShape(unittest.TestCase):
    """The scoped key grants Write on /v1/responses only, so the request and
    reply shapes must be the Responses ones, not chat/completions'."""

    def test_targets_the_responses_endpoint(self):
        self.assertTrue(nl_time.API_URL.endswith("/v1/responses"))

    def test_schema_is_flattened_for_responses_text_format(self):
        schema = nl_time._schema(["Acme"])
        # Responses puts type/name/strict/schema side by side; chat/completions
        # nested them under a "json_schema" key.
        self.assertEqual(schema["type"], "json_schema")
        self.assertIn("schema", schema)
        self.assertNotIn("json_schema", schema)

    def test_extracts_text_from_output_items(self):
        data = {
            "output": [
                {"type": "reasoning", "content": []},
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": '{"intent":"log"}'}],
                },
            ]
        }
        self.assertEqual(nl_time._output_text(data), '{"intent":"log"}')

    def test_concatenates_split_text_parts(self):
        data = {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": '{"intent":'},
                        {"type": "output_text", "text": '"log"}'},
                    ],
                }
            ]
        }
        self.assertEqual(nl_time._output_text(data), '{"intent":"log"}')

    def test_empty_or_malformed_output_yields_empty_string(self):
        self.assertEqual(nl_time._output_text({}), "")
        self.assertEqual(nl_time._output_text({"output": []}), "")
        self.assertEqual(nl_time._output_text({"output": [{"type": "reasoning"}]}), "")


class TestConfigGuards(unittest.TestCase):
    def test_interpret_without_key_raises_config_error(self):
        with self.assertRaises(nl_time.NLConfigError):
            nl_time.interpret("log an hour", ["Acme"], api_key="   ")

    def test_interpret_without_projects_raises_config_error(self):
        with self.assertRaises(nl_time.NLConfigError):
            nl_time.interpret("log an hour", [], api_key="sk-test")

    def test_validate_rejects_empty_key_without_network(self):
        ok, message = nl_time.validate_api_key("")
        self.assertFalse(ok)
        self.assertIn("No key", message)


if __name__ == "__main__":
    unittest.main()
