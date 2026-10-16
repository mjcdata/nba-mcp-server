"""SQLite storage for normalized appearances and their coverage provenance.

Uses only the standard library. No NBA requests, scoring, or MCP code runs here.
"""

from contextlib import contextmanager
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3


SCHEMA_VERSION = 1
SEASON = "2025-26"
SEASON_TYPE = "Regular Season"
COUNT_FIELDS = (
    "points", "field_goals_made", "field_goals_attempted",
    "three_pointers_made", "three_pointers_attempted", "free_throws_made",
    "free_throws_attempted", "offensive_rebounds", "defensive_rebounds",
    "total_rebounds", "assists", "steals", "blocks", "turnovers", "personal_fouls",
)
TEXT_FIELDS = (
    "game_id", "player_id", "player_name", "game_date", "season", "season_type",
    "team_id", "team_abbreviation", "matchup", "result", "source", "retrieved_at",
)
RECORD_FIELDS = TEXT_FIELDS + ("opponent_team_id", "minutes") + COUNT_FIELDS + ("plus_minus",)
COVERAGE_FIELDS = {
    "season", "season_type", "player_count", "appearance_count", "game_count",
    "team_game_count", "checks_passed", "issues", "reference_source", "verified_complete",
}


class DatabaseValidationError(ValueError):
    """The database input is not a valid normalized dataset or query."""


class SchemaVersionError(RuntimeError):
    """A file has an unsupported schema or unrelated existing tables."""


@contextmanager
def _transaction(connection):
    """Own a transaction without committing or rolling back a caller's work."""
    if connection.in_transaction:
        raise DatabaseValidationError("Finish the existing transaction before calling this operation")
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def open_database(path):
    """Open/create a SQLite file (or ':memory:') and initialize schema version 1.

    Return a sqlite3.Connection with dictionary-like rows and foreign keys on.
    The caller must close it; use contextlib.closing. Parent directories must
    exist. Raise SchemaVersionError for unsupported/unrecognized schemas or
    sqlite3.Error for file/SQL failures. Creates tables/indexes only in an empty
    unversioned database; never migrates, deletes data, or makes network calls.
    """
    if sqlite3.sqlite_version_info < (3, 24, 0):
        raise SchemaVersionError("SQLite 3.24+ is required for UPSERT support")
    connection = sqlite3.connect(path, timeout=5, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )}
        if version == SCHEMA_VERSION:
            if not {"ingestions", "player_games"}.issubset(tables):
                raise SchemaVersionError("Version 1 database is missing required tables")
            return connection
        if version != 0 or tables:
            raise SchemaVersionError(f"Refusing unrecognized database schema (version {version})")
        count_columns = ",\n".join(
            f"{field} INTEGER NOT NULL CHECK(typeof({field})='integer' AND {field}>=0)"
            for field in COUNT_FIELDS
        )
        with _transaction(connection):
            connection.execute("""
                CREATE TABLE ingestions (
                    ingestion_id INTEGER PRIMARY KEY,
                    fingerprint TEXT NOT NULL UNIQUE,
                    imported_at TEXT NOT NULL,
                    coverage_json TEXT NOT NULL
                )
            """)
            connection.execute(f"""
                CREATE TABLE player_games (
                    game_id TEXT NOT NULL CHECK(length(game_id)=10 AND game_id GLOB '00225[0-9][0-9][0-9][0-9][0-9]'),
                    player_id TEXT NOT NULL,
                    player_name TEXT NOT NULL CHECK(length(trim(player_name))>0),
                    game_date TEXT NOT NULL CHECK(game_date BETWEEN '2025-07-01' AND '2026-06-30'),
                    season TEXT NOT NULL CHECK(season='2025-26'),
                    season_type TEXT NOT NULL CHECK(season_type='Regular Season'),
                    team_id TEXT NOT NULL,
                    team_abbreviation TEXT NOT NULL CHECK(length(trim(team_abbreviation))>0),
                    matchup TEXT NOT NULL CHECK(length(trim(matchup))>0),
                    result TEXT NOT NULL CHECK(result IN ('W','L')),
                    source TEXT NOT NULL CHECK(length(trim(source))>0),
                    retrieved_at TEXT NOT NULL,
                    opponent_team_id TEXT,
                    minutes REAL NOT NULL CHECK(typeof(minutes) IN ('real','integer') AND minutes>=0),
                    {count_columns},
                    plus_minus INTEGER NOT NULL CHECK(typeof(plus_minus)='integer'),
                    ingestion_id INTEGER NOT NULL REFERENCES ingestions(ingestion_id),
                    PRIMARY KEY (game_id, player_id),
                    CHECK(field_goals_made<=field_goals_attempted),
                    CHECK(three_pointers_made<=three_pointers_attempted),
                    CHECK(three_pointers_made<=field_goals_made),
                    CHECK(three_pointers_attempted<=field_goals_attempted),
                    CHECK(free_throws_made<=free_throws_attempted),
                    CHECK(total_rebounds=offensive_rebounds+defensive_rebounds),
                    CHECK(points=2*field_goals_made+three_pointers_made+free_throws_made)
                )
            """)
            connection.execute("CREATE INDEX player_games_player_date ON player_games(player_id, game_date, game_id)")
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        return connection
    except BaseException:
        connection.close()
        raise


def open_readonly_database(path):
    """Open an existing version-1 SQLite file for queries without creating it.

    Return a row-factory connection; callers must close it. SQLite URI mode=ro
    prevents writes and missing-file creation. Raise SchemaVersionError for an
    unsupported/incomplete schema, or sqlite3.Error for missing/unreadable files.
    No initialization, migration, network calls, or source data changes occur.
    The path is local configuration, never an agent-supplied tool argument.
    """
    uri = Path(path).expanduser().resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            raise SchemaVersionError(f"Expected schema version {SCHEMA_VERSION}, received {version}")
        # Check columns without reading appearances or creating missing objects.
        connection.execute(f"SELECT {', '.join(RECORD_FIELDS)}, ingestion_id FROM player_games LIMIT 0")
        connection.execute("SELECT ingestion_id, fingerprint, imported_at, coverage_json FROM ingestions LIMIT 0")
        return connection
    except BaseException:
        connection.close()
        raise


def _identifier(value, field):
    """Require canonical positive numeric IDs, preserving them as text."""
    if not isinstance(value, str) or not value.isascii() or not value.isdigit() or value.startswith("0"):
        raise DatabaseValidationError(f"{field}: expected a positive numeric string without leading zeros")


def _iso_date(value, field):
    """Validate a canonical calendar date and return it unchanged."""
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError as exc:
        raise DatabaseValidationError(f"{field}: expected YYYY-MM-DD") from exc
    return value


def _timestamp(value):
    """Require an aware UTC retrieval timestamp for safe freshness comparisons."""
    try:
        timestamp = datetime.fromisoformat(value)
        if timestamp.utcoffset() != timezone.utc.utcoffset(timestamp):
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise DatabaseValidationError("retrieved_at: expected an aware UTC ISO timestamp") from exc
    return timestamp


def _validate_dataset(dataset):
    """Check normalized types/relationships and coverage binding before writes."""
    if not isinstance(dataset, dict) or set(dataset) != {"records", "coverage"}:
        raise DatabaseValidationError("Expected a dataset containing records and coverage")
    records, coverage = dataset["records"], dataset["coverage"]
    if not isinstance(records, list) or not records:
        raise DatabaseValidationError("records must be a nonempty list")
    seen = set()
    for row in records:
        if not isinstance(row, dict) or set(row) != set(RECORD_FIELDS):
            raise DatabaseValidationError("Record fields must match the normalized data dictionary")
        for field in TEXT_FIELDS:
            if not isinstance(row[field], str) or not row[field].strip():
                raise DatabaseValidationError(f"{field}: expected nonempty text")
        for field in ("player_id", "team_id"):
            _identifier(row[field], field)
        game_id = row["game_id"]
        if len(game_id) != 10 or not game_id.isascii() or not game_id.isdigit() or not game_id.startswith("00225"):
            raise DatabaseValidationError("game_id: expected a 2025-26 regular-season ID")
        game_date = _iso_date(row["game_date"], "game_date")
        if not "2025-07-01" <= game_date <= "2026-06-30" or row["season"] != SEASON or row["season_type"] != SEASON_TYPE:
            raise DatabaseValidationError("Record is outside the supported season/type")
        _timestamp(row["retrieved_at"])
        if row["result"] not in ("W", "L"):
            raise DatabaseValidationError("result: expected W or L")
        if row["opponent_team_id"] is not None:
            _identifier(row["opponent_team_id"], "opponent_team_id")
        minutes = row["minutes"]
        if type(minutes) not in (int, float) or minutes < 0 or minutes > 1.7976931348623157e308 or not math.isfinite(minutes):
            raise DatabaseValidationError("minutes: expected a finite nonnegative number")
        for field in COUNT_FIELDS + ("plus_minus",):
            value = row[field]
            if type(value) is not int or not -(2**63) <= value < 2**63 or (field != "plus_minus" and value < 0):
                raise DatabaseValidationError(f"{field}: expected an in-range normalized integer")
        for made, attempted in (("field_goals_made", "field_goals_attempted"),
                                ("three_pointers_made", "three_pointers_attempted"),
                                ("free_throws_made", "free_throws_attempted")):
            if row[made] > row[attempted]:
                raise DatabaseValidationError(f"{made} cannot exceed {attempted}")
        if row["three_pointers_made"] > row["field_goals_made"] or row["three_pointers_attempted"] > row["field_goals_attempted"]:
            raise DatabaseValidationError("Three-pointers exceed field goals")
        if row["total_rebounds"] != row["offensive_rebounds"] + row["defensive_rebounds"]:
            raise DatabaseValidationError("Rebounds do not add up")
        if row["points"] != 2 * row["field_goals_made"] + row["three_pointers_made"] + row["free_throws_made"]:
            raise DatabaseValidationError("Points do not match made shots")
        key = game_id, row["player_id"]
        if key in seen:
            raise DatabaseValidationError(f"Duplicate appearance: {key}")
        seen.add(key)
    if not isinstance(coverage, dict) or set(coverage) != COVERAGE_FIELDS:
        raise DatabaseValidationError("Coverage fields must match the Phase 2 report")
    if coverage["season"] != SEASON or coverage["season_type"] != SEASON_TYPE:
        raise DatabaseValidationError("Coverage has the wrong season/type")
    for field in ("player_count", "appearance_count", "game_count", "team_game_count"):
        if type(coverage[field]) is not int or coverage[field] < 0:
            raise DatabaseValidationError(f"coverage {field}: expected a nonnegative integer")
    if coverage["appearance_count"] != len(records) or coverage["player_count"] != len({row["player_id"] for row in records}):
        raise DatabaseValidationError("Coverage counts do not describe these records")
    if type(coverage["checks_passed"]) is not bool or coverage["verified_complete"] is not False:
        raise DatabaseValidationError("Coverage requires boolean checks_passed and verified_complete=False")
    issues = coverage["issues"]
    if not isinstance(issues, list) or any(not isinstance(issue, str) or not issue.strip() for issue in issues):
        raise DatabaseValidationError("Coverage issues must be a list of nonempty strings")
    if coverage["checks_passed"] != (not issues):
        raise DatabaseValidationError("Coverage checks_passed contradicts issues")
    if not isinstance(coverage["reference_source"], str) or not coverage["reference_source"].strip():
        raise DatabaseValidationError("Coverage reference_source is required")


def ingest_dataset(connection, dataset):
    """Atomically upsert a normalized Phase 2 dataset and preserve its report.

    Return ingestion_id, records_processed, and new_ingestion. Identical batches
    reuse a report (input order is irrelevant); source corrections replace the
    current appearance. Older retrieval timestamps reject the entire batch;
    equal timestamps permit correction. Omitted appearances are never deleted.
    Raise DatabaseValidationError for invalid/stale input or a caller-owned
    transaction, and sqlite3.Error for SQL failures. All writes roll back on
    failure. This function trusts, but does not recalculate, the supplied coverage
    report. It performs no network access and does not alter source statistics.
    """
    _validate_dataset(dataset)
    records = sorted(dataset["records"], key=lambda row: (row["game_id"], row["player_id"]))
    canonical = json.dumps({"records": records, "coverage": dataset["coverage"]}, sort_keys=True, allow_nan=False)
    fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
    columns = RECORD_FIELDS + ("ingestion_id",)
    assignments = ", ".join(f"{field}=excluded.{field}" for field in columns if field not in ("game_id", "player_id"))
    sql = f"INSERT INTO player_games ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)}) ON CONFLICT(game_id,player_id) DO UPDATE SET {assignments}"
    with _transaction(connection):
        existing = connection.execute("SELECT ingestion_id FROM ingestions WHERE fingerprint=?", (fingerprint,)).fetchone()
        if existing is None:
            cursor = connection.execute(
                "INSERT INTO ingestions(fingerprint,imported_at,coverage_json) VALUES (?,?,?)",
                (fingerprint, datetime.now(timezone.utc).isoformat(), json.dumps(dataset["coverage"], sort_keys=True)),
            )
            ingestion_id = cursor.lastrowid
        else:
            ingestion_id = existing[0]
        for row in records:
            current = connection.execute(
                "SELECT retrieved_at FROM player_games WHERE game_id=? AND player_id=?",
                (row["game_id"], row["player_id"]),
            ).fetchone()
            if current is not None and _timestamp(row["retrieved_at"]) < _timestamp(current[0]):
                raise DatabaseValidationError(f"Stale retrieval for {(row['game_id'], row['player_id'])}")
            connection.execute(sql, tuple(row[field] for field in RECORD_FIELDS) + (ingestion_id,))
    return {"ingestion_id": ingestion_id, "records_processed": len(records), "new_ingestion": existing is None}


def _query_result(connection, where, parameters, *, limit, offset):
    """Read appearances and relevant reports in one consistent SELECT snapshot."""
    rows = connection.execute(
        f"SELECT p.*, i.imported_at, i.coverage_json FROM player_games p JOIN ingestions i USING(ingestion_id) WHERE {where} ORDER BY p.game_date, p.game_id, p.player_id LIMIT ? OFFSET ?",
        (*parameters, limit, offset),
    ).fetchall()
    records, ingestions = [], {}
    for row in rows:
        record = {field: row[field] for field in RECORD_FIELDS}
        record["ingestion_id"] = row["ingestion_id"]
        records.append(record)
        ingestions[row["ingestion_id"]] = {
            "ingestion_id": row["ingestion_id"], "imported_at": row["imported_at"],
            "coverage": json.loads(row["coverage_json"]),
        }
    return {"records": records, "ingestions": list(ingestions.values())}


def get_player_games(connection, player_id, *, start_date=None, end_date=None, limit=100, offset=0):
    """Return a player's chronologically ordered records and ingestion reports.

    Dates are optional inclusive YYYY-MM-DD bounds; limit is 1..1000 and offset
    is nonnegative. An unknown player/empty range returns two empty lists.
    Raise DatabaseValidationError for invalid inputs or sqlite3.Error for SQL
    failures. Uses bound parameters and performs no writes or network calls.
    Reports describe original import batches, not just this filtered page.
    """
    _identifier(player_id, "player_id")
    if type(limit) is not int or not 1 <= limit <= 1000 or type(offset) is not int or not 0 <= offset < 2**63:
        raise DatabaseValidationError("limit must be 1..1000 and offset a nonnegative SQLite integer")
    clauses, parameters = ["p.player_id=?"], [player_id]
    for value, comparison in ((start_date, ">="), (end_date, "<=")):
        if value is not None:
            _iso_date(value, "date bound")
            clauses.append(f"p.game_date{comparison}?")
            parameters.append(value)
    if start_date is not None and end_date is not None and start_date > end_date:
        raise DatabaseValidationError("start_date must not follow end_date")
    return _query_result(connection, " AND ".join(clauses), parameters, limit=limit, offset=offset)


def get_game_performance(connection, game_id, player_id):
    """Return one raw-stat appearance with its report, using the query envelope.

    No score is calculated. Unknown keys return empty records/ingestions lists.
    Raise DatabaseValidationError for invalid keys or sqlite3.Error for SQL
    failures. This read-only operation has no network effects.
    """
    _identifier(player_id, "player_id")
    if not isinstance(game_id, str) or len(game_id) != 10 or not game_id.isascii() or not game_id.isdigit() or not game_id.startswith("00225"):
        raise DatabaseValidationError("game_id: expected a 2025-26 regular-season ID")
    return _query_result(connection, "p.game_id=? AND p.player_id=?", (game_id, player_id), limit=1, offset=0)
