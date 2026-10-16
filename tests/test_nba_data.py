"""Offline Phase 2 tests; fixture identities and performances are synthetic."""

from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from requests.exceptions import ConnectionError, HTTPError, Timeout

from nba_mcp import nba_data as data


FIXTURE = Path(__file__).parent / "fixtures" / "player_game_logs.json"


class NormalizationTests(unittest.TestCase):
    """Verify normalization, trade history, and rejection of invalid inputs."""

    def setUp(self):
        self.rows = json.loads(FIXTURE.read_text())

    def test_fixture_preserves_trade_and_brief_appearance(self):
        result = data.normalize_player_game_logs(self.rows[::-1])
        self.assertEqual([r["team_id"] for r in result], ["1610612742", "1610612738"])
        self.assertEqual(result[0]["player_id"], result[1]["player_id"])
        self.assertEqual(result[0]["game_id"], "0022500001")
        self.assertEqual(result[0]["game_date"], "2025-10-21")
        self.assertEqual(result[0]["minutes"], 25.5)
        self.assertAlmostEqual(result[1]["minutes"], 1 / 60)
        self.assertEqual(result[0]["plus_minus"], -7)
        self.assertEqual(result[0]["retrieved_at"], result[1]["retrieved_at"])
        self.assertTrue(result[0]["retrieved_at"].endswith("+00:00"))
        self.assertIsNone(result[0]["opponent_team_id"])
        self.assertIsInstance(result[0]["points"], int)

    def test_numeric_string_counts_and_zero_minutes(self):
        self.rows[0].update(PTS="15.0", MIN=0, PLUS_MINUS="-7")
        result = data.normalize_player_game_logs(self.rows)
        self.assertEqual(result[0]["points"], 15)
        self.assertEqual(result[0]["minutes"], 0)

    def test_invalid_source_values(self):
        cases = [
            ("SEASON_YEAR", "2024-25"), ("GAME_ID", "0042500001"),
            ("GAME_ID", "0062500001"), ("GAME_ID", 225000001),
            ("GAME_DATE", "2027-01-01"), ("GAME_DATE", "invalid"),
            ("PLAYER_ID", 0), ("PLAYER_ID", True), ("TEAM_ID", None),
            ("PLAYER_NAME", ""), ("WL", None), ("MIN", None),
            ("MIN", "DNP"), ("MIN", "1:60"), ("MIN", float("nan")),
            ("MIN", -1), ("PTS", None), ("PTS", float("inf")),
            ("PTS", 1.5), ("PTS", True), ("AST", -1),
            ("FGM", 11), ("FG3M", 6), ("FG3A", 11),
            ("FTM", 5), ("REB", 10), ("PTS", 16),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                rows = deepcopy(self.rows)
                rows[0][field] = value
                with self.assertRaises(data.DataValidationError):
                    data.normalize_player_game_logs(rows)

    def test_empty_malformed_and_duplicate_rows(self):
        for rows in ([], None, [None], [{}], [self.rows[0], self.rows[0]]):
            with self.subTest(rows=rows):
                with self.assertRaises(data.DataValidationError):
                    data.normalize_player_game_logs(rows)

    def test_missing_stat_is_not_defaulted(self):
        del self.rows[0]["PLUS_MINUS"]
        with self.assertRaises(data.DataValidationError):
            data.normalize_player_game_logs(self.rows)


class RetrievalTests(unittest.TestCase):
    """Verify public request parameters and bounded failure handling offline."""

    def setUp(self):
        self.rows = json.loads(FIXTURE.read_text())
        self.patcher = patch.object(data, "PlayerGameLogs")
        self.endpoint = self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.endpoint.return_value.get_normalized_dict.return_value = {"PlayerGameLogs": self.rows}

    def test_fixed_season_league_and_total_parameters(self):
        self.assertEqual(len(data.fetch_player_game_logs(timeout=12, retries=0)), 2)
        self.endpoint.assert_called_once_with(
            timeout=12, season_nullable="2025-26", season_type_nullable="Regular Season",
            league_id_nullable="00", per_mode_simple_nullable="Totals",
        )

    @patch.object(data.time, "sleep")
    def test_timeout_then_success(self, sleep):
        self.endpoint.side_effect = [Timeout("slow"), self.endpoint.return_value]
        self.assertEqual(len(data.fetch_player_game_logs()), 2)
        self.assertEqual(self.endpoint.call_count, 2)
        sleep.assert_called_once_with(0.5)

    @patch.object(data.time, "sleep")
    def test_connection_failure_exhausts_retry_limit(self, sleep):
        self.endpoint.side_effect = ConnectionError("offline")
        with self.assertRaises(data.NBADataError):
            data.fetch_player_game_logs(retries=2)
        self.assertEqual(self.endpoint.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.5, 1.0])

    def test_http_and_parser_failures_are_not_retried(self):
        for failure in (HTTPError("denied"), ValueError("bad JSON"), KeyError("schema")):
            with self.subTest(failure=failure):
                self.endpoint.reset_mock()
                self.endpoint.side_effect = failure
                with self.assertRaises(data.NBADataError):
                    data.fetch_player_game_logs()
                self.endpoint.assert_called_once()

    def test_bad_responses_are_not_retried(self):
        for response in ({}, {"PlayerGameLogs": []}, {"PlayerGameLogs": None}, []):
            with self.subTest(response=response):
                self.endpoint.reset_mock()
                self.endpoint.return_value.get_normalized_dict.return_value = response
                with self.assertRaises(data.DataValidationError):
                    data.fetch_player_game_logs()
                self.endpoint.assert_called_once()

    def test_invalid_request_options_fail_before_network(self):
        for options in ({"timeout": 0}, {"timeout": float("nan")}, {"timeout": True},
                        {"retries": 3}, {"retries": -1}, {"retries": True}):
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    data.fetch_player_game_logs(**options)
        self.endpoint.assert_not_called()

    @patch.object(data, "LeagueGameLog")
    def test_combined_fetch_and_team_parameters(self, team_endpoint):
        teams = [{**row, "SEASON_ID": "22025"} for row in self.rows]
        team_endpoint.return_value.get_normalized_dict.return_value = {"LeagueGameLog": teams}
        result = data.fetch_season_dataset(timeout=12, retries=0)
        self.assertEqual(len(result["records"]), 2)
        self.assertFalse(result["coverage"]["checks_passed"])
        team_endpoint.assert_called_once_with(
            timeout=12, season="2025-26", season_type_all_star="Regular Season",
            league_id="00", player_or_team_abbreviation="T",
        )


class CoverageTests(unittest.TestCase):
    """Exercise reconciliation independently of the upstream transport."""

    def setUp(self):
        rows = json.loads(FIXTURE.read_text())
        self.records = data.normalize_player_game_logs(rows)
        self.teams = [{**row, "SEASON_ID": "22025"} for row in rows]

    def test_small_matching_sample_is_not_complete(self):
        result = data.reconcile_season_coverage(self.records, self.teams)
        self.assertEqual(result["player_count"], 1)
        self.assertEqual(result["appearance_count"], 2)
        self.assertFalse(result["checks_passed"])
        self.assertFalse(result["verified_complete"])
        self.assertIn("Expected 1230 regular-season games, received 2", result["issues"])

    def test_missing_unexpected_and_mismatched_rows(self):
        self.teams[0]["PTS"] += 1
        self.records[0]["game_date"] = "2025-10-22"
        self.records[1]["team_id"] = "1234"
        issues = data.reconcile_season_coverage(self.records, self.teams)["issues"]
        for fragment in ("points:", "Date mismatch", "Missing player team-game", "Unexpected player team-game"):
            self.assertTrue(any(fragment in issue for issue in issues), fragment)

    def test_invalid_reference_and_duplicate_appearances(self):
        for teams in ([], [None], [{**self.teams[0], "SEASON_ID": "42025"}],
                      [self.teams[0], self.teams[0]]):
            with self.subTest(teams=teams):
                with self.assertRaises(data.DataValidationError):
                    data.reconcile_season_coverage(self.records, teams)
        with self.assertRaises(data.DataValidationError):
            data.reconcile_season_coverage([self.records[0], self.records[0]], self.teams)

    def test_full_synthetic_game_set_passes_consistency_checks(self):
        records, teams = [], []
        for game in range(1, 1231):
            game_id = f"00225{game:05d}"
            for index, (template, record) in enumerate(zip(self.teams, self.records)):
                team_id = str(100 + ((game - 1) % 15) * 2 + index)
                teams.append({**template, "TEAM_ID": team_id, "GAME_ID": game_id, "GAME_DATE": "2026-01-01"})
                for player in range(5):
                    appearance = {**record, "player_id": f"{team_id}{player}", "team_id": team_id,
                                  "game_id": game_id, "game_date": "2026-01-01"}
                    if player:
                        appearance.update(dict.fromkeys(data.RECONCILED_FIELDS, 0))
                    records.append(appearance)
        result = data.reconcile_season_coverage(records, teams)
        self.assertTrue(result["checks_passed"], result["issues"])
        self.assertEqual(result["team_game_count"], 2460)
        self.assertFalse(result["verified_complete"])


if __name__ == "__main__":
    unittest.main()
