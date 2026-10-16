"""Local, read-only MCP adapters for stored NBA statistics and Game Score."""

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from fastmcp import FastMCP
from fastmcp.tools import ToolResult

from nba_mcp.database import (
    DatabaseValidationError, SchemaVersionError, get_game_performance,
    get_player_games, open_readonly_database,
)
from nba_mcp.scoring import ScoringValidationError, evaluate_game_performance


READ_ONLY = {"readOnlyHint": True, "destructiveHint": False,
             "idempotentHint": True, "openWorldHint": False}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "data": {"type": "object"},
                   "error": {"type": "object"}},
    "required": ["ok"],
}


def _error(code, message):
    """Build a structured application error with the MCP error flag set."""
    return ToolResult(structured_content={"ok": False, "error": {"code": code, "message": message}}, is_error=True)


def _query(database_path, operation, *args, evaluate=False, require_match=False, **kwargs):
    """Delegate a read with per-call connection ownership and safe error mapping.

    Keep the database envelope, including full source records and ingestion
    reports. Optional evaluation delegates all mathematics to scoring.py.
    Never initialize/import data or expose filesystem/SQL exception details.
    """
    try:
        with closing(open_readonly_database(database_path)) as connection:
            result = operation(connection, *args, **kwargs)
        if require_match and not result["records"]:
            return _error("NOT_FOUND", "No stored appearance matches the supplied player and game IDs.")
        if evaluate:
            for record in result["records"]:
                record["evaluation"] = evaluate_game_performance(record)
        return ToolResult(structured_content={"ok": True, "data": result})
    except DatabaseValidationError as exc:
        return _error("INVALID_ARGUMENT", str(exc))
    except ScoringValidationError as exc:
        return _error("INVALID_STATISTICS", str(exc))
    except SchemaVersionError:
        return _error("UNSUPPORTED_DATABASE", "The configured database does not have the supported schema. Initialize/import it using the Python database functions.")
    except json.JSONDecodeError:
        return _error("INVALID_DATABASE", "A stored ingestion report cannot be decoded. Check the configured database.")
    except sqlite3.Error:
        return _error("DATABASE_UNAVAILABLE", "The configured database is missing, unreadable, busy, or invalid. Check the local database configuration.")


def create_server(database_path):
    """Return a FastMCP server bound to one owner-configured local database path.

    Register three read-only tools; open/close a SQLite read-only connection in
    each call's worker thread. Construction performs no data retrieval or schema
    writes. Tools cannot choose another file, refresh data, or change the formula.
    Invalid tool inputs are rejected by FastMCP or the Python business functions.
    Unexpected exception details are masked by FastMCP.
    """
    database_path = Path(database_path).expanduser().resolve()
    mcp = FastMCP(
        "NBA MCP Server", version="0.1.0", strict_input_validation=True,
        mask_error_details=True,
        instructions=("Read stored 2025-26 NBA regular-season appearances using numeric NBA player IDs. "
                      "Retain ingestion coverage findings with statistics; scoring does not certify source quality. "
                      "Game Score is unmodified Hollinger Game Score; plus-minus is separate context. "
                      "These tools never fetch NBA data or import/modify the database."),
    )

    @mcp.tool(name="get_player_games", annotations=READ_ONLY, output_schema=OUTPUT_SCHEMA)
    def player_history(player_id: str, start_date: str | None = None,
                       end_date: str | None = None, limit: int = 100, offset: int = 0) -> ToolResult:
        """Read a player's chronological game statistics and original coverage reports.

        Args:
            player_id: Positive numeric NBA player ID as a string, not a name.
            start_date: Optional inclusive YYYY-MM-DD lower bound.
            end_date: Optional inclusive YYYY-MM-DD upper bound.
            limit: Page size from 1 to 1000; defaults to 100.
            offset: Nonnegative pagination offset; defaults to zero.

        Empty histories succeed with empty lists. Records identify their ingestion
        reports; those reports describe original batches, not this filtered page.
        Returns a structured success/error envelope without modifying storage.
        """
        return _query(database_path, get_player_games, player_id,
                      start_date=start_date, end_date=end_date, limit=limit, offset=offset)

    @mcp.tool(name="get_game_statistics", annotations=READ_ONLY, output_schema=OUTPUT_SCHEMA)
    def game_statistics(game_id: str, player_id: str) -> ToolResult:
        """Read one stored appearance's raw statistics and original coverage report.

        Args:
            game_id: Ten-digit regular-season NBA game ID starting 00225.
            player_id: Positive numeric NBA player ID as a string.

        Returns the original database envelope or a NOT_FOUND error; no score is
        calculated, no NBA requests occur, and source quality is not certified.
        """
        return _query(database_path, get_game_performance, game_id, player_id, require_match=True)

    @mcp.tool(name="evaluate_game_performance", annotations=READ_ONLY, output_schema=OUTPUT_SCHEMA)
    def game_evaluation(game_id: str, player_id: str) -> ToolResult:
        """Evaluate a stored appearance using unmodified Hollinger Game Score.

        Args:
            game_id: Ten-digit regular-season NBA game ID starting 00225.
            player_id: Positive numeric NBA player ID as a string.

        Each source record gains an evaluation containing game_score, separate
        plus_minus, formula_version, and formula_source. Original identifiers,
        statistics, and ingestion coverage remain present. No caller-supplied
        statistics, weights, true-shooting adjustment, or storage writes.
        """
        return _query(database_path, get_game_performance, game_id, player_id,
                      evaluate=True, require_match=True)

    return mcp


def main():
    """Parse an explicit database path and serve MCP over local stdio.

    Run as `nba-mcp-server --database PATH` or `python -m nba_mcp.server` with
    the same argument. Stdout is reserved for MCP messages; no HTTP listener is
    started. Argument errors exit through argparse. Missing data is reported by
    tool calls rather than implicitly creating an empty database.
    """
    parser = argparse.ArgumentParser(description="Read-only NBA statistics and Game Score MCP server")
    parser.add_argument("--database", required=True, type=Path, help="Existing NBA SQLite database file")
    arguments = parser.parse_args()
    create_server(arguments.database).run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
