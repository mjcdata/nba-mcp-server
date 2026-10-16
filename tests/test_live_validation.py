"""Offline checks for the opt-in live verifier's reconciliation/report helpers."""

import json
from pathlib import Path
import unittest

from live_validate import compare_player_totals, reference_game_score
from nba_mcp.nba_data import STAT_FIELDS, normalize_player_game_logs


class LiveValidationHelperTests(unittest.TestCase):
    """Prevent source-summary discrepancies from being called successful checks."""

    def setUp(self):
        """Prepare two appearances and a matching synthetic player-season total."""
        rows = json.loads((Path(__file__).parent / "fixtures/player_game_logs.json").read_text())
        self.records = normalize_player_game_logs(rows)
        self.summary = {"PLAYER_ID": 1001, "GP": 2}
        self.summary.update({upstream: sum(record[field] for record in self.records)
                             for field, upstream in STAT_FIELDS.items()})

    def test_matching_player_counts_and_totals(self):
        """Pass only when appearance counts, player sets, and statistics agree."""
        report = compare_player_totals(self.records, [self.summary])
        self.assertTrue(report["checks_passed"])
        self.assertTrue(report["appearance_counts_match"])
        self.assertEqual(report["summary_player_count"], 1)

    def test_missing_players_counts_and_stat_discrepancies(self):
        """Detect a missing zero-stat appearance, not just mismatched scoring totals."""
        self.summary["GP"] = 3
        self.summary["FGA"] += 1
        report = compare_player_totals(self.records, [self.summary])
        self.assertFalse(report["checks_passed"])
        self.assertFalse(report["appearance_counts_match"])
        self.assertEqual({item["field"] for item in report["mismatches"]}, {"appearances", "field_goals_attempted"})
        other = {**self.summary, "PLAYER_ID": 9999}
        report = compare_player_totals(self.records, [other])
        self.assertEqual(report["missing_players"], ["9999"])
        self.assertEqual(report["unexpected_players"], ["1001"])

    def test_ambiguous_or_empty_summaries_fail(self):
        """Reject duplicate player totals, invalid GP/counts, and empty references."""
        for summaries in ([], [self.summary, self.summary], [{**self.summary, "GP": None}],
                          [{**self.summary, "GP": 0}], [{**self.summary, "FGA": "10"}]):
            with self.subTest(summaries=summaries):
                with self.assertRaises(ValueError):
                    compare_player_totals(self.records, summaries)

    def test_decimal_reference_matches_worked_example(self):
        """Check the reference implementation against independent fixture arithmetic."""
        # 15+2-7-0.4+0.7+1.2+1+2.1-1.2-2 = 11.4.
        self.assertEqual(str(reference_game_score(self.records[0])), "11.4")
        self.assertEqual(reference_game_score(self.records[1]), 0)


if __name__ == "__main__":
    unittest.main()
