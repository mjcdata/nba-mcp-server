"""Offline upstream-to-MCP scenarios; synthetic data never contacts the NBA."""

from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from requests.exceptions import Timeout

from nba_mcp import database as db
from nba_mcp import nba_data


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    """Test retrieval boundaries, persisted corrections, and agent-use sequences."""

    def setUp(self):
        """Create an empty temporary workspace and source-shaped fixture responses."""
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "workflow.sqlite3"
        self.rows = json.loads((Path(__file__).parent / "fixtures/player_game_logs.json").read_text())
        self.teams = [{**row, "SEASON_ID": "22025"} for row in self.rows]

    def retrieve(self, rows=None):
        """Mock only upstream endpoint constructors; use production normalization."""
        with patch.object(nba_data, "PlayerGameLogs") as players, patch.object(nba_data, "LeagueGameLog") as teams:
            players.return_value.get_normalized_dict.return_value = {"PlayerGameLogs": rows or self.rows}
            teams.return_value.get_normalized_dict.return_value = {"LeagueGameLog": self.teams}
            return nba_data.fetch_season_dataset(retries=0)

    def client(self):
        """Return a real subprocess MCP client reading this test's persisted file."""
        return Client(StdioTransport(command=sys.executable,
            args=["-m", "nba_mcp.server", "--database", str(self.path)],
            cwd=str(Path(__file__).resolve().parents[1]), keep_alive=False), timeout=20)

    async def test_retrieval_to_agent_evaluation_and_repeat_import(self):
        """Traverse the full path and retain trade history, scoring, and quality caveats."""
        dataset = self.retrieve()
        with closing(db.open_database(self.path)) as connection:
            first = db.ingest_dataset(connection, dataset)
            repeated = db.ingest_dataset(connection, dataset)
            self.assertEqual(first["ingestion_id"], repeated["ingestion_id"])
            self.assertFalse(repeated["new_ingestion"])
        async with self.client() as client:
            history = (await client.call_tool("get_player_games", {"player_id": "1001"})).structured_content["data"]
            self.assertEqual([row["team_abbreviation"] for row in history["records"]], ["DAL", "BOS"])
            evaluation = (await client.call_tool("evaluate_game_performance", {
                "game_id": history["records"][0]["game_id"], "player_id": "1001"})).structured_content["data"]
            self.assertEqual(evaluation["records"][0]["evaluation"]["game_score"], 11.4)
            self.assertEqual(evaluation["records"][0]["evaluation"]["plus_minus"], -7)
            self.assertEqual(evaluation["ingestions"][0]["coverage"], dataset["coverage"])
            self.assertFalse(evaluation["ingestions"][0]["coverage"]["verified_complete"])
            # An agent can paginate to a brief zero-stat appearance and score it.
            page = (await client.call_tool("get_player_games", {
                "player_id": "1001", "limit": 1, "offset": 1})).structured_content["data"]
            brief = (await client.call_tool("evaluate_game_performance", {
                "game_id": page["records"][0]["game_id"], "player_id": "1001"})).structured_content["data"]
            self.assertEqual(brief["records"][0]["evaluation"]["game_score"], 0.0)

    async def test_corrected_retrieval_is_visible_after_reopen_and_server_restart(self):
        """Propagate a source correction without duplicate rows or stale evaluations."""
        original = self.retrieve()
        for row in original["records"]:
            row["retrieved_at"] = "2026-10-14T12:00:00+00:00"
        with closing(db.open_database(self.path)) as connection:
            db.ingest_dataset(connection, original)
        corrected_rows = deepcopy(self.rows)
        corrected_rows[0]["AST"] = 4
        corrected_rows[0]["PLUS_MINUS"] = 50
        corrected = self.retrieve(corrected_rows)
        for row in corrected["records"]:
            row["retrieved_at"] = "2026-10-15T12:00:00+00:00"
        with closing(db.open_database(self.path)) as connection:
            db.ingest_dataset(connection, corrected)
        async with self.client() as client:
            result = (await client.call_tool("evaluate_game_performance", {
                "game_id": "0022500001", "player_id": "1001"})).structured_content["data"]
            # One more assist adds 0.7: 11.4 -> 12.1; plus-minus has no effect.
            self.assertEqual(result["records"][0]["evaluation"]["game_score"], 12.1)
            self.assertEqual(result["records"][0]["evaluation"]["plus_minus"], 50)
            history = (await client.call_tool("get_player_games", {"player_id": "1001"})).structured_content["data"]
            self.assertEqual(len(history["records"]), 2)

    async def test_bad_refresh_leaves_previous_agent_results_available(self):
        """Reject malformed upstream data and preserve the last successful import."""
        dataset = self.retrieve()
        with closing(db.open_database(self.path)) as connection:
            db.ingest_dataset(connection, dataset)
        broken = deepcopy(self.rows)
        broken[0]["PTS"] = None
        with self.assertRaises(nba_data.DataValidationError):
            self.retrieve(broken)
        with patch.object(nba_data, "PlayerGameLogs", side_effect=Timeout("offline")):
            with self.assertRaises(nba_data.NBADataError):
                nba_data.fetch_season_dataset(retries=0)
        async with self.client() as client:
            result = await client.call_tool("evaluate_game_performance", {
                "game_id": "0022500001", "player_id": "1001"})
            self.assertEqual(result.structured_content["data"]["records"][0]["evaluation"]["game_score"], 11.4)
            missing = await client.call_tool("get_game_statistics", {
                "game_id": "0022500999", "player_id": "1001"}, raise_on_error=False)
            self.assertTrue(missing.is_error)
            self.assertEqual(missing.structured_content["error"]["code"], "NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
