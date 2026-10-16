"""NBA regular-season retrieval and validation, independent of storage and MCP."""

from collections import defaultdict
from datetime import date, datetime, timezone
import math
import time

from nba_api.stats.endpoints.leaguegamelog import LeagueGameLog
from nba_api.stats.endpoints.playergamelogs import PlayerGameLogs
from requests.exceptions import ConnectionError, RequestException, Timeout


SEASON = "2025-26"
SEASON_TYPE = "Regular Season"
SOURCE = "NBA Stats / playergamelogs / nba_api 1.11.4"
STAT_FIELDS = {
    "points": "PTS", "field_goals_made": "FGM",
    "field_goals_attempted": "FGA", "three_pointers_made": "FG3M",
    "three_pointers_attempted": "FG3A", "free_throws_made": "FTM",
    "free_throws_attempted": "FTA", "offensive_rebounds": "OREB",
    "defensive_rebounds": "DREB", "total_rebounds": "REB",
    "assists": "AST", "steals": "STL", "blocks": "BLK",
    "turnovers": "TOV", "personal_fouls": "PF", "plus_minus": "PLUS_MINUS",
}
RECONCILED_FIELDS = (
    "points", "field_goals_made", "field_goals_attempted",
    "three_pointers_made", "three_pointers_attempted",
    "free_throws_made", "free_throws_attempted",
)


class DataValidationError(ValueError):
    """Upstream rows do not satisfy the supported season/data contract."""


class NBADataError(RuntimeError):
    """A bounded upstream request failed; no partial dataset is returned."""


def _integer(value, field, *, signed=False):
    """Parse finite whole counts without accepting booleans or missing values."""
    if isinstance(value, bool) or value is None:
        raise DataValidationError(f"{field}: expected an integer, got {value!r}")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DataValidationError(f"{field}: invalid integer {value!r}") from exc
    if not math.isfinite(number) or not number.is_integer() or (number < 0 and not signed):
        raise DataValidationError(f"{field}: invalid integer {value!r}")
    return int(number)


def _identifier(value, field):
    """Normalize positive numeric NBA player/team identifiers as strings."""
    number = _integer(value, field)
    if number <= 0:
        raise DataValidationError(f"{field}: must be positive")
    return str(number)


def _text(value, field):
    """Require a nonempty source string."""
    if not isinstance(value, str) or not value.strip():
        raise DataValidationError(f"{field}: missing text")
    return value.strip()


def _game_identity(row):
    """Validate the target regular-season game prefix and source date."""
    game_id = row.get("GAME_ID")
    if not isinstance(game_id, str) or len(game_id) != 10 or not game_id.isascii() or not game_id.isdigit() or not game_id.startswith("00225"):
        raise DataValidationError("GAME_ID: expected a 10-digit 2025-26 regular-season ID starting 00225")
    raw_date = _text(row.get("GAME_DATE"), "GAME_DATE")
    try:
        game_date = datetime.fromisoformat(raw_date).date()
    except ValueError as exc:
        raise DataValidationError(f"GAME_DATE: invalid ISO date {raw_date!r}") from exc
    if not date(2025, 7, 1) <= game_date <= date(2026, 6, 30):
        raise DataValidationError("GAME_DATE: outside the target season year")
    return game_id, game_date.isoformat()


def _minutes(value):
    """Normalize numeric minutes or MM:SS, retaining legitimate zero minutes."""
    if value is None or isinstance(value, bool):
        raise DataValidationError("MIN: missing/invalid participation; DNP is not zero")
    try:
        if isinstance(value, str) and ":" in value:
            minutes, seconds = value.split(":")
            minute_count = _integer(minutes, "MIN")
            second_count = _integer(seconds, "MIN seconds")
            if second_count >= 60:
                raise DataValidationError("MIN seconds: must be below 60")
            number = minute_count + second_count / 60
        else:
            number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DataValidationError(f"MIN: invalid minutes {value!r}") from exc
    if not math.isfinite(number) or number < 0:
        raise DataValidationError("MIN: must be finite and nonnegative")
    return number


def normalize_player_game_logs(rows):
    """Return validated appearance dictionaries for 2025-26 regular season.

    Accept a list of upstream PlayerGameLogs dictionaries. Raise
    DataValidationError for empty input, duplicates, DNP/missing statistics,
    wrong-season rows, or invalid types/relationships. Never silently drop rows.
    A single UTC normalization timestamp is attached; no network/storage effects.
    """
    if not isinstance(rows, list) or not rows:
        raise DataValidationError("Expected a nonempty list of player game logs")
    retrieved_at = datetime.now(timezone.utc).isoformat()
    records, keys = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise DataValidationError("Each source row must be a dictionary")
        if row.get("SEASON_YEAR") != SEASON:
            raise DataValidationError("SEASON_YEAR: expected 2025-26")
        game_id, game_date = _game_identity(row)
        player_id = _identifier(row.get("PLAYER_ID"), "PLAYER_ID")
        key = game_id, player_id
        if key in keys:
            raise DataValidationError(f"Duplicate player appearance: {key}")
        keys.add(key)
        result = row.get("WL")
        if result not in ("W", "L"):
            raise DataValidationError("WL: expected a completed game result W or L")
        record = {
            "player_id": player_id,
            "player_name": _text(row.get("PLAYER_NAME"), "PLAYER_NAME"),
            "game_id": game_id, "game_date": game_date,
            "season": SEASON, "season_type": SEASON_TYPE,
            "team_id": _identifier(row.get("TEAM_ID"), "TEAM_ID"),
            "team_abbreviation": _text(row.get("TEAM_ABBREVIATION"), "TEAM_ABBREVIATION"),
            "opponent_team_id": None,
            "matchup": _text(row.get("MATCHUP"), "MATCHUP"),
            "result": result, "minutes": _minutes(row.get("MIN")),
            "source": SOURCE, "retrieved_at": retrieved_at,
        }
        for field, upstream in STAT_FIELDS.items():
            record[field] = _integer(row.get(upstream), upstream, signed=field == "plus_minus")
        for made, attempted in (("field_goals_made", "field_goals_attempted"),
                                ("three_pointers_made", "three_pointers_attempted"),
                                ("free_throws_made", "free_throws_attempted")):
            if record[made] > record[attempted]:
                raise DataValidationError(f"{made}: cannot exceed {attempted}")
        if record["three_pointers_made"] > record["field_goals_made"] or record["three_pointers_attempted"] > record["field_goals_attempted"]:
            raise DataValidationError("Three-pointers cannot exceed total field goals")
        if record["total_rebounds"] != record["offensive_rebounds"] + record["defensive_rebounds"]:
            raise DataValidationError("REB: must equal OREB + DREB")
        if record["points"] != 2 * record["field_goals_made"] + record["three_pointers_made"] + record["free_throws_made"]:
            raise DataValidationError("PTS: inconsistent with made shots")
        records.append(record)
    return sorted(records, key=lambda record: (record["game_date"], record["game_id"], record["player_id"]))


def _request_rows(factory, dataset, *, timeout, retries, **parameters):
    """Perform bounded requests; retry connection/timeouts only, never bad data."""
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    if type(retries) is not int or not 0 <= retries <= 2:
        raise ValueError("retries must be an integer between 0 and 2")
    for attempt in range(retries + 1):
        try:
            response = factory(timeout=timeout, **parameters).get_normalized_dict()
        except (Timeout, ConnectionError) as exc:
            if attempt == retries:
                raise NBADataError(f"{dataset}: failed after {attempt + 1} attempt(s): {type(exc).__name__}") from exc
            time.sleep(0.5 * (2 ** attempt))
            continue
        except (RequestException, ValueError, KeyError) as exc:
            raise NBADataError(f"{dataset}: request or response parsing failed: {type(exc).__name__}") from exc
        if not isinstance(response, dict) or not isinstance(response.get(dataset), list) or not response[dataset]:
            raise DataValidationError(f"{dataset}: missing or empty result set")
        return response[dataset]


def fetch_player_game_logs(*, timeout=30, retries=1):
    """Retrieve and normalize all target-season player appearances over HTTPS.

    Uses NBA league 00, Regular Season, Totals, and no player/team/date filters.
    timeout is the requests socket timeout per attempt, not an overall deadline;
    retries is limited to 0..2. Raises NBADataError on transport/parser failure
    or DataValidationError on invalid rows. No persistence or completeness claim.
    """
    rows = _request_rows(
        PlayerGameLogs, "PlayerGameLogs", timeout=timeout, retries=retries,
        season_nullable=SEASON, season_type_nullable=SEASON_TYPE,
        league_id_nullable="00", per_mode_simple_nullable="Totals",
    )
    return normalize_player_game_logs(rows)


def reconcile_season_coverage(records, team_rows):
    """Compare normalized player appearances with raw LeagueGameLog team rows.

    Return counts and issues for all 1,230 regular-season games, two teams per
    game, matching game/team sets, dates, and additive shooting totals. Invalid
    reference rows/duplicate keys raise DataValidationError; mismatches are
    reported. A passing report is a consistency check, not independent proof
    that zero-stat appearances are present. No network or storage effects.
    """
    if not isinstance(records, list) or not records or not isinstance(team_rows, list) or not team_rows:
        raise DataValidationError("Coverage requires nonempty player and team lists")
    expected, dates = {}, {}
    for row in team_rows:
        if not isinstance(row, dict) or str(row.get("SEASON_ID")) != "22025":
            raise DataValidationError("Team SEASON_ID: expected 22025")
        game_id, game_date = _game_identity(row)
        key = game_id, _identifier(row.get("TEAM_ID"), "TEAM_ID")
        if key in expected:
            raise DataValidationError(f"Duplicate team game: {key}")
        expected[key] = {field: _integer(row.get(STAT_FIELDS[field]), field) for field in RECONCILED_FIELDS}
        dates[key] = game_date
    actual = defaultdict(lambda: dict.fromkeys(RECONCILED_FIELDS, 0))
    appearance_counts = defaultdict(int)
    issues, seen, players = [], set(), set()
    for record in records:
        key = record["game_id"], record["team_id"]
        appearance = record["game_id"], record["player_id"]
        if appearance in seen:
            raise DataValidationError(f"Duplicate normalized appearance: {appearance}")
        if record["season"] != SEASON or record["season_type"] != SEASON_TYPE:
            raise DataValidationError("Normalized appearance has wrong season/type")
        seen.add(appearance)
        appearance_counts[key] += 1
        players.add(record["player_id"])
        if key in dates and record["game_date"] != dates[key]:
            issues.append(f"Date mismatch: {appearance}")
        for field in RECONCILED_FIELDS:
            actual[key][field] += _integer(record[field], field)
    for key in sorted(expected.keys() - actual.keys()):
        issues.append(f"Missing player team-game: {key}")
    for key in sorted(actual.keys() - expected.keys()):
        issues.append(f"Unexpected player team-game: {key}")
    for key in sorted(expected.keys() & actual.keys()):
        if appearance_counts[key] < 5:
            issues.append(f"{key}: fewer than five player appearances")
        for field in RECONCILED_FIELDS:
            if actual[key][field] != expected[key][field]:
                issues.append(f"{key} {field}: players={actual[key][field]}, team={expected[key][field]}")
    games = defaultdict(set)
    team_games = defaultdict(int)
    for game_id, team_id in expected:
        games[game_id].add(team_id)
        team_games[team_id] += 1
    if len(games) != 1230:
        issues.append(f"Expected 1230 regular-season games, received {len(games)}")
    for game_id, teams in sorted(games.items()):
        if len(teams) != 2:
            issues.append(f"{game_id}: expected two teams, received {len(teams)}")
    if len(team_games) != 30:
        issues.append(f"Expected 30 teams, received {len(team_games)}")
    for team_id, count in sorted(team_games.items()):
        if count != 82:
            issues.append(f"{team_id}: expected 82 games, received {count}")
    return {
        "season": SEASON, "season_type": SEASON_TYPE,
        "player_count": len(players), "appearance_count": len(records),
        "game_count": len(games), "team_game_count": len(expected),
        "checks_passed": not issues, "issues": issues,
        "reference_source": "NBA Stats / leaguegamelog / nba_api 1.11.4",
        "verified_complete": False,
    }


def fetch_season_dataset(*, timeout=30, retries=1):
    """Fetch player logs and team reference logs, returning records and coverage.

    Makes two sequential HTTPS requests (plus bounded transient retries).
    Raises NBADataError/DataValidationError without returning a partial result.
    Coverage discrepancies remain explicit in the returned report. No database,
    scoring, MCP, background refresh, or file writes occur.
    """
    records = fetch_player_game_logs(timeout=timeout, retries=retries)
    team_rows = _request_rows(
        LeagueGameLog, "LeagueGameLog", timeout=timeout, retries=retries,
        season=SEASON, season_type_all_star=SEASON_TYPE,
        league_id="00", player_or_team_abbreviation="T",
    )
    return {"records": records, "coverage": reconcile_season_coverage(records, team_rows)}
