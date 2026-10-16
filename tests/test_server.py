"""Offline MCP contract tests and a real local stdio client smoke test."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from nba_mcp import database as db
from nba_mcp.nba_data import normalize_player_game_logs, reconcile_season_coverage
from nba_mcp.scoring import ScoringValidationError
from nba_mcp.server import create_server


class ServerTests(unittest.IsolatedAsyncioTestCase):
    """Exercise tool discovery, schemas, results, failures, and stdio startup."""

    def setUp(self):
        """Populate a temporary version-1 database from the synthetic fixtures."""
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "nba space #1.sqlite3"
        rows = json.loads((Path(__file__).parent / "fixtures/player_game_logs.json").read_text())
        records = normalize_player_game_logs(rows)
        teams = [{**row, "SEASON_ID": "22025"} for row in rows]
        self.dataset = {"records": records, "coverage": reconcile_season_coverage(records, teams)}
        with closing(db.open_database(self.path)) as connection:
            db.ingest_dataset(connection, self.dataset)
        self.server = create_server(self.path)

    async def test_tool_discovery_schemas_and_annotations(self):
        """Advertise only the three documented tools and owner-independent inputs."""
        async with Client(self.server) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
        self.assertEqual(set(tools), {"get_player_games", "get_game_statistics", "evaluate_game_performance"})
        for tool in tools.values():
            self.assertTrue(tool.description)
            self.assertTrue(tool.annotations.read_only_hint)
            self.assertFalse(tool.annotations.open_world_hint)
            self.assertNotIn("database_path", tool.input_schema["properties"])
            self.assertNotIn("weight", tool.input_schema["properties"])
            self.assertEqual(tool.input_schema["properties"]["player_id"]["type"], "string")
            self.assertIsNotNone(tool.output_schema)
        self.assertEqual(tools["get_player_games"].input_schema["properties"]["limit"]["default"], 100)

    async def test_history_preserves_source_and_failed_coverage(self):
        """Retain trade history, source identifiers, and failing batch findings."""
        async with Client(self.server) as client:
            result = await client.call_tool("get_player_games", {"player_id": "1001"})
        payload = result.structured_content
        self.assertFalse(result.is_error)
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["data"]["records"]), 2)
        self.assertEqual([row["team_abbreviation"] for row in payload["data"]["records"]], ["DAL", "BOS"])
        self.assertEqual(payload["data"]["ingestions"][0]["coverage"], self.dataset["coverage"])

    async def test_raw_game_and_evaluation_delegate_without_writes(self):
        """Add the tested score separately while retaining raw data and provenance."""
        before = self.path.read_bytes()
        arguments = {"game_id": "0022500001", "player_id": "1001"}
        async with Client(self.server) as client:
            raw = (await client.call_tool("get_game_statistics", arguments)).structured_content["data"]
            evaluated = (await client.call_tool("evaluate_game_performance", arguments)).structured_content["data"]
        evaluation = evaluated["records"][0].pop("evaluation")
        self.assertEqual(evaluated, raw)
        self.assertEqual(evaluation["game_score"], 11.4)
        self.assertEqual(evaluation["plus_minus"], -7)
        self.assertEqual(evaluation["formula_version"], "hollinger-game-score-v1")
        self.assertIn("basketball-reference.com", evaluation["formula_source"])
        self.assertEqual(self.path.read_bytes(), before)

    async def test_history_date_bounds_pagination_and_empty_results(self):
        """Use inclusive date bounds and preserve empty-history success semantics."""
        async with Client(self.server) as client:
            bounded = await client.call_tool("get_player_games", {
                "player_id": "1001", "start_date": "2025-10-21", "end_date": "2025-10-21"})
            paged = await client.call_tool("get_player_games", {"player_id": "1001", "limit": 1, "offset": 1})
            empty = await client.call_tool("get_player_games", {"player_id": "9999"})
        self.assertEqual(bounded.structured_content["data"]["records"][0]["game_id"], "0022500001")
        self.assertEqual(paged.structured_content["data"]["records"][0]["game_id"], "0022500800")
        self.assertEqual(empty.structured_content, {"ok": True, "data": {"records": [], "ingestions": []}})

    async def test_unknown_appearance_returns_structured_not_found(self):
        """Flag missing individual games as tool errors rather than fabricating rows."""
        async with Client(self.server) as client:
            for name in ("get_game_statistics", "evaluate_game_performance"):
                result = await client.call_tool(name, {"game_id": "0022500999", "player_id": "1001"}, raise_on_error=False)
                self.assertTrue(result.is_error)
                self.assertEqual(result.structured_content["error"]["code"], "NOT_FOUND")

    async def test_semantic_validation_errors_are_structured(self):
        """Map domain validation failures to stable INVALID_ARGUMENT responses."""
        async with Client(self.server) as client:
            for arguments in ({"player_id": "abc"}, {"player_id": "1001", "limit": 0},
                              {"player_id": "1001", "offset": -1},
                              {"player_id": "1001", "start_date": "not-a-date"},
                              {"player_id": "1001", "start_date": "2026-01-01", "end_date": "2025-01-01"}):
                with self.subTest(arguments=arguments):
                    result = await client.call_tool("get_player_games", arguments, raise_on_error=False)
                    self.assertTrue(result.is_error)
                    self.assertEqual(result.structured_content["error"]["code"], "INVALID_ARGUMENT")

    async def test_protocol_rejects_wrong_types_missing_and_extra_arguments(self):
        """Reject coercion, unknown keywords, and missing schema-required fields."""
        async with Client(self.server) as client:
            for arguments in ({}, {"player_id": 1001}, {"player_id": "1001", "limit": "2"},
                              {"player_id": "1001", "limit": True},
                              {"player_id": "1001", "database_path": "/tmp/other.db"}):
                with self.subTest(arguments=arguments):
                    result = await client.call_tool("get_player_games", arguments, raise_on_error=False)
                    self.assertTrue(result.is_error)

    async def test_missing_database_is_not_created_and_details_are_hidden(self):
        """Report missing storage without creating an empty database or leaking paths."""
        missing = self.path.parent / "missing.sqlite3"
        async with Client(create_server(missing)) as client:
            result = await client.call_tool("get_player_games", {"player_id": "1001"}, raise_on_error=False)
        self.assertTrue(result.is_error)
        self.assertEqual(result.structured_content["error"]["code"], "DATABASE_UNAVAILABLE")
        self.assertNotIn(str(missing), str(result.content))
        self.assertFalse(missing.exists())

    async def test_unsupported_schema_and_corrupt_report_errors(self):
        """Keep unsupported-schema and malformed stored-report errors actionable."""
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("PRAGMA user_version=99")
        async with Client(self.server) as client:
            result = await client.call_tool("get_player_games", {"player_id": "1001"}, raise_on_error=False)
            self.assertEqual(result.structured_content["error"]["code"], "UNSUPPORTED_DATABASE")
            with closing(sqlite3.connect(self.path)) as connection:
                connection.execute("PRAGMA user_version=1")
                connection.execute("UPDATE ingestions SET coverage_json='invalid JSON'")
                connection.commit()
            result = await client.call_tool("get_player_games", {"player_id": "1001"}, raise_on_error=False)
            self.assertTrue(result.is_error)
            self.assertEqual(result.structured_content["error"]["code"], "INVALID_DATABASE")

    async def test_scoring_validation_is_exposed_as_safe_domain_error(self):
        """Map scoring failures without falsely claiming a successful evaluation."""
        with patch("nba_mcp.server.evaluate_game_performance", side_effect=ScoringValidationError("Missing required statistic: points")):
            async with Client(self.server) as client:
                result = await client.call_tool("evaluate_game_performance", {
                    "player_id": "1001", "game_id": "0022500001"}, raise_on_error=False)
        self.assertTrue(result.is_error)
        self.assertEqual(result.structured_content["error"]["code"], "INVALID_STATISTICS")

    def test_readonly_connection_cannot_write_or_initialize(self):
        """Enforce read-only behavior at SQLite level, including URI-escaped paths."""
        with closing(db.open_readonly_database(self.path)) as connection:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM player_games")
        missing = self.path.parent / "absent.sqlite3"
        with self.assertRaises(sqlite3.OperationalError):
            db.open_readonly_database(missing)
        self.assertFalse(missing.exists())

    async def test_stdio_subprocess_client_smoke(self):
        """Start the module over real stdio, discover tools, and evaluate a fixture."""
        transport = StdioTransport(command=sys.executable, args=[
            "-m", "nba_mcp.server", "--database", str(self.path)],
            cwd=str(Path(__file__).resolve().parents[1]), keep_alive=False)
        async with Client(transport, timeout=20) as client:
            self.assertEqual(len(await client.list_tools()), 3)
            result = await client.call_tool("evaluate_game_performance", {
                "game_id": "0022500001", "player_id": "1001"})
            self.assertEqual(result.structured_content["data"]["records"][0]["evaluation"]["game_score"], 11.4)
            self.assertFalse(result.structured_content["data"]["ingestions"][0]["coverage"]["verified_complete"])


if __name__ == "__main__":
    unittest.main()
