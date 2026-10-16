"""Explicit opt-in Phase 6 live verification; never run by unittest discovery.

Run with --database and --report. Fetches NBA Stats, imports validated records,
checks a separate player-season summary, and exercises the real stdio server.
Reports source discrepancies without correcting statistics or claiming complete
coverage. NBADataError/validation/network failures abort rather than fabricate data.
"""

import argparse
import asyncio
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from nba_api.stats.endpoints.leaguedashplayerstats import LeagueDashPlayerStats
from requests.exceptions import RequestException

from nba_mcp import database as db
from nba_mcp.nba_data import SEASON, SEASON_TYPE, STAT_FIELDS, fetch_season_dataset
from nba_mcp.scoring import calculate_game_score


def compare_player_totals(records, summaries):
    """Compare appearance counts and additive stats with another NBA result set.

    Return player-set, GP, and statistics differences. Reject duplicate IDs or
    malformed summary counts instead of silently aggregating ambiguous rows.
    This cross-endpoint check shares a provider and is not independent ground truth.
    """
    appearances = Counter(row["player_id"] for row in records)
    totals = defaultdict(Counter)
    for row in records:
        for field in STAT_FIELDS:
            totals[row["player_id"]][field] += row[field]
    expected, mismatches = {}, []
    for row in summaries:
        if type(row.get("GP")) is not int or row["GP"] < 0:
            raise ValueError("Player summary GP must be a nonnegative integer")
        if row["GP"] == 0:
            continue
        player_id = str(row["PLAYER_ID"])
        if player_id in expected:
            raise ValueError(f"Duplicate player-season summary: {player_id}")
        expected[player_id] = row
        if appearances[player_id] != row["GP"]:
            mismatches.append({"player_id": player_id, "field": "appearances",
                               "logs": appearances[player_id], "summary": row["GP"]})
        for field, upstream in STAT_FIELDS.items():
            value = row[upstream]
            if type(value) is not int:
                raise ValueError(f"Player summary {upstream} must be an integer")
            if totals[player_id][field] != value:
                mismatches.append({"player_id": player_id, "field": field,
                                   "logs": totals[player_id][field], "summary": value})
    if not expected:
        raise ValueError("Player-season summary is empty")
    missing = sorted(expected.keys() - appearances.keys())
    unexpected = sorted(appearances.keys() - expected.keys())
    return {
        "source": "NBA Stats / leaguedashplayerstats / Totals / Regular Season / 2025-26",
        "summary_player_count": len(expected), "missing_players": missing,
        "unexpected_players": unexpected, "mismatches": mismatches,
        "appearance_counts_match": not missing and not unexpected and not any(
            item["field"] == "appearances" for item in mismatches),
        "checks_passed": not missing and not unexpected and not mismatches,
    }


def reference_game_score(row):
    """Evaluate published coefficients with Decimal, independently of integer tenths."""
    return (
        Decimal(row["points"]) + Decimal("0.4") * row["field_goals_made"]
        - Decimal("0.7") * row["field_goals_attempted"]
        - Decimal("0.4") * (row["free_throws_attempted"] - row["free_throws_made"])
        + Decimal("0.7") * row["offensive_rebounds"] + Decimal("0.3") * row["defensive_rebounds"]
        + row["steals"] + Decimal("0.7") * row["assists"] + Decimal("0.7") * row["blocks"]
        - Decimal("0.4") * row["personal_fouls"] - row["turnovers"]
    )


async def check_stdio(database_path, records):
    """Run live-backed agent-use sequences through a real subprocess MCP client.

    Check first/best/worst/zero-minute/traded/Houston examples when present,
    page a real player's history, and verify error handling and provenance.
    Return reproducible evidence; this is not a separate LLM/desktop-client eval.
    """
    selections = [records[0], max(records, key=calculate_game_score), min(records, key=calculate_game_score)]
    teams = defaultdict(set)
    for row in records:
        teams[row["player_id"]].add(row["team_id"])
    for predicate in (
        lambda row: row["minutes"] == 0,
        lambda row: len(teams[row["player_id"]]) > 1,
        lambda row: row["game_id"] == "0022500792" and row["team_id"] == "1610612745",
    ):
        match = next((row for row in records if predicate(row)), None)
        if match is not None:
            selections.append(match)
    samples, seen = [], set()
    transport = StdioTransport(command=sys.executable,
        args=["-m", "nba_mcp.server", "--database", str(database_path)],
        cwd=str(Path(__file__).resolve().parents[1]), keep_alive=False)
    async with Client(transport, timeout=30) as client:
        names = {tool.name for tool in await client.list_tools()}
        assert names == {"get_player_games", "get_game_statistics", "evaluate_game_performance"}
        for row in selections:
            key = row["game_id"], row["player_id"]
            if key in seen:
                continue
            seen.add(key)
            result = await client.call_tool("evaluate_game_performance", {"game_id": key[0], "player_id": key[1]})
            data = result.structured_content["data"]
            actual = data["records"][0]
            assert {field: actual[field] for field in db.RECORD_FIELDS} == row
            assert Decimal(str(actual["evaluation"]["game_score"])) == reference_game_score(row)
            assert actual["evaluation"]["plus_minus"] == row["plus_minus"]
            assert data["ingestions"] and not data["ingestions"][0]["coverage"]["verified_complete"]
            samples.append({"game_id": key[0], "player_id": key[1], "player_name": row["player_name"],
                            "game_date": row["game_date"], "statistics": row, "evaluation": actual["evaluation"]})
        player_id = selections[0]["player_id"]
        history = (await client.call_tool("get_player_games", {"player_id": player_id, "limit": 1000})).structured_content["data"]
        assert len(history["records"]) == sum(row["player_id"] == player_id for row in records)
        page = (await client.call_tool("get_player_games", {"player_id": player_id, "limit": 1})).structured_content["data"]
        assert page["records"] == history["records"][:1]
        missing = await client.call_tool("get_game_statistics", {"game_id": "0022599999", "player_id": player_id}, raise_on_error=False)
        assert missing.is_error and missing.structured_content["error"]["code"] == "NOT_FOUND"
        invalid = await client.call_tool("get_player_games", {"player_id": player_id, "limit": 0}, raise_on_error=False)
        assert invalid.is_error and invalid.structured_content["error"]["code"] == "INVALID_ARGUMENT"
    return {"tools": sorted(names), "samples": samples, "history_player_id": player_id,
            "history_appearance_count": len(history["records"]), "pagination_passed": True,
            "error_scenarios_passed": True, "desktop_or_separate_llm_tested": False}


async def validate(database_path, timeout):
    """Fetch/import the full season, reconcile it, and validate local agent access.

    Write only the explicitly configured SQLite file; return the evidence report.
    Imports use existing transactional validation. A failed retrieval leaves the
    previous database untouched. Summary failure is recorded and never called a
    pass. Assertion failures stop verification; source discrepancies remain facts.
    """
    print("Fetching player and team game logs...", file=sys.stderr, flush=True)
    dataset = fetch_season_dataset(timeout=timeout, retries=0)
    records = dataset["records"]
    print("Checking separate player-season totals...", file=sys.stderr, flush=True)
    try:
        summaries = LeagueDashPlayerStats(
            season=SEASON, season_type_all_star=SEASON_TYPE, league_id_nullable="00",
            per_mode_detailed="Totals", measure_type_detailed_defense="Base", timeout=timeout,
        ).get_normalized_dict()["LeagueDashPlayerStats"]
        summary_check = compare_player_totals(records, summaries)
    except (RequestException, ValueError, KeyError) as exc:
        summary_check = {"checks_passed": False, "unavailable": True, "error_type": type(exc).__name__}
    print("Importing and verifying repeatability...", file=sys.stderr, flush=True)
    with closing(db.open_database(database_path)) as connection:
        imported = db.ingest_dataset(connection, dataset)
        repeated = db.ingest_dataset(connection, dataset)
        assert repeated["ingestion_id"] == imported["ingestion_id"] and not repeated["new_ingestion"]
        stored = [dict(row) for row in connection.execute("SELECT * FROM player_games")]
        # This validation run expects a full-season file, not a union with stale removed rows.
        assert len(stored) == len(records)
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    print("Checking scores and local stdio scenarios...", file=sys.stderr, flush=True)
    assert all(Decimal(str(calculate_game_score(row))) == reference_game_score(row) for row in records)
    before = hashlib.sha256(database_path.read_bytes()).hexdigest()
    mcp_check = await check_stdio(database_path, records)
    after = hashlib.sha256(database_path.read_bytes()).hexdigest()
    assert before == after
    return {
        "validated_at": datetime.now(timezone.utc).isoformat(), "season": SEASON,
        "database_path": str(database_path), "database_sha256": after,
        "date_range": [min(row["game_date"] for row in records), max(row["game_date"] for row in records)],
        "coverage": dataset["coverage"], "player_summary_check": summary_check,
        "ingestion": imported, "repeat_import_passed": True, "database_integrity": "ok",
        "score_reference_count": len(records), "all_score_references_match": True,
        "mcp_check": mcp_check, "database_unchanged_by_mcp": True,
        "verified_complete": False,
    }


def main():
    """Run opt-in live checks and save a JSON evidence report on successful execution."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()
    report = asyncio.run(validate(args.database.resolve(), args.timeout))
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"report": str(args.report), "coverage": report["coverage"],
                      "player_summary_check": report["player_summary_check"],
                      "score_reference_count": report["score_reference_count"],
                      "mcp_sample_count": len(report["mcp_check"]["samples"])}, indent=2))


if __name__ == "__main__":
    main()
