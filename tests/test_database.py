"""Offline Phase 3 persistence tests using synthetic fixtures and temporary files."""

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from nba_mcp import database as db
from nba_mcp.nba_data import normalize_player_game_logs, reconcile_season_coverage


def fixture_dataset():
    """Build a deterministic normalized batch and a genuinely failing coverage report."""
    rows = json.loads((Path(__file__).parent / "fixtures/player_game_logs.json").read_text())
    records = normalize_player_game_logs(rows)
    for record in records:
        record["retrieved_at"] = "2026-10-14T12:00:00+00:00"
    team_rows = [{**row, "SEASON_ID": "22025"} for row in rows]
    return {"records": records, "coverage": reconcile_season_coverage(records, team_rows)}


class DatabaseTests(unittest.TestCase):
    """Verify atomic ingestion, correction rules, provenance, and query boundaries."""

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "nba.sqlite3"
        self.connection = db.open_database(self.path)
        self.addCleanup(self.connection.close)
        self.dataset = fixture_dataset()

    def counts(self):
        """Return appearance/report counts for rollback and repeatability assertions."""
        return tuple(self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                     for table in ("player_games", "ingestions"))

    def test_round_trip_and_reopen(self):
        summary = db.ingest_dataset(self.connection, self.dataset)
        self.assertTrue(summary["new_ingestion"])
        self.assertEqual(summary["records_processed"], 2)
        self.assertEqual(self.counts(), (2, 1))
        self.connection.close()
        self.connection = db.open_database(self.path)
        self.addCleanup(self.connection.close)
        result = db.get_player_games(self.connection, "1001")
        for actual, expected in zip(result["records"], self.dataset["records"]):
            self.assertEqual({key: actual[key] for key in db.RECORD_FIELDS}, expected)
        self.assertEqual(result["ingestions"][0]["coverage"], self.dataset["coverage"])
        self.assertFalse(result["ingestions"][0]["coverage"]["checks_passed"])
        self.assertIsNone(result["records"][0]["opponent_team_id"])
        self.assertEqual(result["records"][0]["plus_minus"], -7)

    def test_repeated_import_and_reordered_input_reuse_report(self):
        first = db.ingest_dataset(self.connection, self.dataset)
        reordered = deepcopy(self.dataset)
        reordered["records"].reverse()
        second = db.ingest_dataset(self.connection, reordered)
        self.assertEqual(second["ingestion_id"], first["ingestion_id"])
        self.assertFalse(second["new_ingestion"])
        self.assertEqual(self.counts(), (2, 1))

    def test_newer_correction_updates_without_duplicates(self):
        first = db.ingest_dataset(self.connection, self.dataset)
        corrected = deepcopy(self.dataset)
        corrected["records"][0]["field_goals_attempted"] = 11
        for row in corrected["records"]:
            row["retrieved_at"] = "2026-10-14T13:00:00+00:00"
        corrected["coverage"]["issues"].append("Source correction under review")
        second = db.ingest_dataset(self.connection, corrected)
        result = db.get_game_performance(self.connection, "0022500001", "1001")
        self.assertEqual(result["records"][0]["field_goals_attempted"], 11)
        self.assertEqual(result["records"][0]["ingestion_id"], second["ingestion_id"])
        self.assertNotEqual(first["ingestion_id"], second["ingestion_id"])
        self.assertEqual(self.counts(), (2, 2))
        self.assertEqual(json.loads(self.connection.execute(
            "SELECT coverage_json FROM ingestions WHERE ingestion_id=?", (first["ingestion_id"],)
        ).fetchone()[0]), self.dataset["coverage"])

    def test_equal_timestamp_can_correct_source_fields(self):
        db.ingest_dataset(self.connection, self.dataset)
        corrected = deepcopy(self.dataset)
        corrected["records"][0]["assists"] = 4
        db.ingest_dataset(self.connection, corrected)
        result = db.get_game_performance(self.connection, "0022500001", "1001")
        self.assertEqual(result["records"][0]["assists"], 4)
        self.assertEqual(self.counts(), (2, 2))

    def test_stale_second_row_rolls_back_earlier_update_and_report(self):
        db.ingest_dataset(self.connection, self.dataset)
        stale = deepcopy(self.dataset)
        stale["records"][0]["assists"] = 99
        stale["records"][1]["retrieved_at"] = "2026-10-13T12:00:00+00:00"
        with self.assertRaisesRegex(db.DatabaseValidationError, "Stale"):
            db.ingest_dataset(self.connection, stale)
        self.assertEqual(self.counts(), (2, 1))
        result = db.get_player_games(self.connection, "1001")
        self.assertEqual(result["records"][0]["assists"], 3)
        self.assertFalse(self.connection.in_transaction)

    def test_partial_import_does_not_delete_and_returns_relevant_reports(self):
        db.ingest_dataset(self.connection, self.dataset)
        partial = deepcopy(self.dataset)
        partial["records"] = [partial["records"][0]]
        partial["records"][0]["assists"] = 4
        partial["coverage"]["appearance_count"] = 1
        partial["coverage"]["issues"].append("Partial batch")
        db.ingest_dataset(self.connection, partial)
        result = db.get_player_games(self.connection, "1001")
        self.assertEqual(len(result["records"]), 2)
        self.assertEqual(len(result["ingestions"]), 2)
        self.assertEqual(result["records"][1]["team_id"], "1610612738")

    def test_sql_failure_rolls_back_whole_batch(self):
        self.connection.execute("""
            CREATE TRIGGER reject_second BEFORE INSERT ON player_games
            WHEN NEW.game_id='0022500800'
            BEGIN SELECT RAISE(ABORT, 'simulated storage failure'); END
        """)
        with self.assertRaises(sqlite3.IntegrityError):
            db.ingest_dataset(self.connection, self.dataset)
        self.assertEqual(self.counts(), (0, 0))
        self.assertFalse(self.connection.in_transaction)

    def test_caller_transaction_is_not_committed_or_rolled_back(self):
        self.connection.execute("BEGIN")
        self.connection.execute("CREATE TABLE caller_work(value TEXT)")
        with self.assertRaisesRegex(db.DatabaseValidationError, "existing transaction"):
            db.ingest_dataset(self.connection, self.dataset)
        self.assertTrue(self.connection.in_transaction)
        self.connection.rollback()
        self.assertEqual(self.counts(), (0, 0))

    def test_invalid_record_values_leave_no_writes(self):
        cases = [
            ("player_id", "001001"), ("team_id", 123), ("game_id", "0042500001"),
            ("game_date", "2026-02-30"), ("game_date", "2027-01-01"),
            ("season", "2024-25"), ("season_type", "Playoffs"), ("player_name", ""),
            ("result", None), ("minutes", float("nan")), ("minutes", -1),
            ("minutes", True), ("minutes", 10**400), ("points", "15"), ("points", None),
            ("plus_minus", 2**63), ("assists", -1), ("assists", True),
            ("retrieved_at", "2026-10-14T12:00:00"),
            ("retrieved_at", "2026-10-14T12:00:00-05:00"),
            ("opponent_team_id", "not-an-id"), ("total_rebounds", 100),
            ("field_goals_made", 11), ("three_pointers_made", 6),
            ("three_pointers_attempted", 11), ("free_throws_made", 5), ("points", 16),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                invalid = deepcopy(self.dataset)
                invalid["records"][1][field] = value
                with self.assertRaises(db.DatabaseValidationError):
                    db.ingest_dataset(self.connection, invalid)
                self.assertEqual(self.counts(), (0, 0))

    def test_bad_dataset_shapes_and_duplicates(self):
        for invalid in (None, {}, {"records": [], "coverage": self.dataset["coverage"]},
                        {"records": [None], "coverage": self.dataset["coverage"]},
                        {"records": [self.dataset["records"][0]] * 2, "coverage": self.dataset["coverage"]}):
            with self.subTest(dataset=invalid):
                with self.assertRaises(db.DatabaseValidationError):
                    db.ingest_dataset(self.connection, invalid)
        invalid = deepcopy(self.dataset)
        del invalid["records"][0]["points"]
        with self.assertRaises(db.DatabaseValidationError):
            db.ingest_dataset(self.connection, invalid)
        self.assertEqual(self.counts(), (0, 0))

    def test_invalid_coverage_is_rejected(self):
        cases = [("season", "2024-25"), ("appearance_count", 9), ("player_count", 9),
                 ("game_count", True), ("team_game_count", -1), ("checks_passed", True),
                 ("verified_complete", True), ("verified_complete", 0),
                 ("reference_source", ""), ("issues", [None]), ("issues", "failure")]
        for field, value in cases:
            with self.subTest(field=field):
                invalid = deepcopy(self.dataset)
                invalid["coverage"][field] = value
                with self.assertRaises(db.DatabaseValidationError):
                    db.ingest_dataset(self.connection, invalid)
                self.assertEqual(self.counts(), (0, 0))

    def test_query_boundaries_trade_history_and_pagination(self):
        db.ingest_dataset(self.connection, self.dataset)
        result = db.get_player_games(self.connection, "1001", start_date="2025-10-21", end_date="2025-10-21")
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["records"][0]["team_abbreviation"], "DAL")
        result = db.get_player_games(self.connection, "1001", limit=1, offset=1)
        self.assertEqual(result["records"][0]["team_abbreviation"], "BOS")
        for result in (db.get_player_games(self.connection, "9999"),
                       db.get_player_games(self.connection, "1001", start_date="2026-03-01"),
                       db.get_game_performance(self.connection, "0022500999", "1001")):
            self.assertEqual(result, {"records": [], "ingestions": []})

    def test_query_validation_and_parameter_safety(self):
        options = [{"limit": 0}, {"limit": 1001}, {"limit": True}, {"offset": -1},
                   {"offset": 2**63}, {"start_date": "invalid"},
                   {"start_date": "2026-01-01", "end_date": "2025-01-01"}]
        for option in options:
            with self.subTest(option=option):
                with self.assertRaises(db.DatabaseValidationError):
                    db.get_player_games(self.connection, "1001", **option)
        with self.assertRaises(db.DatabaseValidationError):
            db.get_player_games(self.connection, "1001' OR 1=1 --")
        with self.assertRaises(db.DatabaseValidationError):
            db.get_game_performance(self.connection, "bad-id", "1001")
        self.dataset["records"][0]["player_name"] = "O'Brien'); DROP TABLE player_games; --"
        db.ingest_dataset(self.connection, self.dataset)
        self.assertEqual(db.get_player_games(self.connection, "1001")["records"][0]["player_name"],
                         self.dataset["records"][0]["player_name"])
        self.assertEqual(self.counts(), (2, 1))

    def test_database_constraints_and_foreign_keys(self):
        db.ingest_dataset(self.connection, self.dataset)
        for sql in ("UPDATE player_games SET points=-1", "UPDATE player_games SET assists=1.5",
                    "UPDATE player_games SET ingestion_id=999", "UPDATE player_games SET season='2024-25'",
                    "UPDATE player_games SET total_rebounds=99", "UPDATE player_games SET minutes='invalid'",
                    "DELETE FROM ingestions"):
            with self.subTest(sql=sql):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.connection.execute(sql)
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute("INSERT INTO player_games SELECT * FROM player_games LIMIT 1")
        self.assertEqual(self.counts(), (2, 1))
        self.assertEqual(self.connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(self.connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_initialization_is_repeatable_and_versioned(self):
        other = db.open_database(self.path)
        try:
            self.assertEqual(other.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(other.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        finally:
            other.close()

    def test_unsupported_or_unrelated_database_is_not_modified(self):
        for name, sql in (("future.sqlite3", "PRAGMA user_version=99"),
                          ("unrelated.sqlite3", "CREATE TABLE unrelated(value TEXT)")):
            with self.subTest(name=name):
                path = self.path.parent / name
                connection = sqlite3.connect(path)
                connection.execute(sql)
                connection.commit()
                connection.close()
                with self.assertRaises(db.SchemaVersionError):
                    db.open_database(path)
                connection = sqlite3.connect(path)
                try:
                    self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='player_games'").fetchone())
                finally:
                    connection.close()


if __name__ == "__main__":
    unittest.main()
