# NBA MCP Server

A phased Python project that will let AI agents retrieve NBA player statistics and evaluate individual game performance. The initial dataset is intended to cover every player with a regular-season appearance in the **2025–2026 NBA season** (season key `2025-26`). Playoffs and preseason are outside the initial scope.

GitHub repository: [mjcdata/nba-mcp-server](https://github.com/mjcdata/nba-mcp-server).

## Status and objectives

All six phases have completed their local implementation/validation scope. The server has a populated 2025–26 database and reproducible offline/live checks. Live reconciliation retains one team-log field-goal-attempt discrepancy; dataset completeness is not certified. Separate desktop/LLM acceptance and release/deployment are not approved or claimed.

## Phase tracker

| Phase | Status |
| --- | --- |
| 1. Project Setup and Documentation | Complete |
| 2. NBA API Integration | Complete; source consistency qualified |
| 3. SQLite Database Implementation | Complete |
| 4. Performance Scoring Implementation | Complete |
| 5. MCP Server Implementation | Complete |
| 6. Integration and Agent Testing | Local workflow validation complete; limitations recorded |

Phase 6 steps: review complete; offline end-to-end tests complete; live retrieval/import/reconciliation complete; stdio agent-use scenarios complete; evidence and release recommendation documented. Any further release, deployment, desktop-client configuration, or significant feature expansion requires owner approval.

The project retrieves player statistics, persists them locally, and exposes stored statistics and unmodified Hollinger Game Score through MCP. Plus-minus is returned separately as context and does not affect the score. There is no true-shooting adjustment or caller-supplied weight.

## Planned technology stack

| Technology | Purpose | Introduction |
| --- | --- | --- |
| Python 3.11+ | Application and standard-library tests | Phase 1 |
| nba_api 1.11.4 | Retrieve NBA statistics | Phase 2 (implemented) |
| SQLite (`sqlite3`) | Local persistence | Phase 3 (implemented) |
| FastMCP 4.1.0 | Local stdio MCP tools | Phase 5 (implemented) |

Direct runtime dependencies are `nba_api==1.11.4` and `fastmcp==4.1.0`, pinned to verified releases. nba_api brings requests, NumPy, and pandas; FastMCP supplies the MCP server/client and validation dependencies. This project uses dictionary responses without DataFrame operations. Storage uses standard-library `sqlite3` with SQLite 3.24+; no separate database service is needed. Transitive dependency versions are not locked.

## Planned capabilities

- Retrieve and normalize player game logs for the target regular season.
- Store game records with stable player and game identifiers.
- Calculate unmodified Hollinger Game Score and show plus-minus separately as context.
- Query player histories and individual performances through MCP tools.
- Provide source, season, and scoring provenance with results.

## Setup

Install Python 3.11 or later, then run from the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
```

On Windows, activate with `.venv\Scripts\activate`. Editable installation needs access to dependencies and the setuptools build backend (normally downloaded by pip). Routine tests are offline, use temporary databases, and require no API credentials or preexisting dataset.

## Retrieve and inspect the target season

After installation, run this Python code explicitly when network access is available:

```python
from nba_mcp.nba_data import fetch_season_dataset

dataset = fetch_season_dataset(timeout=30, retries=1)
print(dataset["coverage"])
records = dataset["records"]
```

This makes two sequential NBA Stats requests. It returns in-memory dictionaries and writes no files. `checks_passed` reports reconciliation results; `verified_complete` remains false because aggregate checks cannot prove every zero-stat appearance is present. Inspect `issues` before using the dataset. See the work plan for the observed discrepancy. Exceptions distinguish invalid source data (`DataValidationError`) from transport/parser failures (`NBADataError`). Timeout is per socket operation, not an overall deadline; at most two retries are allowed, and only connection failures/timeouts are retried. No automatic background requests occur.

## Store and query a retrieved dataset

Continue with the `dataset` above:

```python
from contextlib import closing
from nba_mcp.database import open_database, ingest_dataset, get_player_games, get_game_performance

with closing(open_database("nba.sqlite3")) as connection:
    summary = ingest_dataset(connection, dataset)
    player_id = dataset["records"][0]["player_id"]
    game_id = dataset["records"][0]["game_id"]
    history = get_player_games(connection, player_id, limit=100)
    performance = get_game_performance(connection, game_id, player_id)
    print(summary)
    print(performance["records"])
    print(performance["ingestions"])  # Includes the original coverage report.
```

Opening creates the schema if the file is empty; no import occurs until `ingest_dataset` is called. Failed coverage checks are stored with the statistics for inspection, not silently corrected or promoted to complete. Invalid rows/coverage structure reject the entire batch. Re-imports update keyed appearances; the same dataset reuses its ingestion report. Older retrievals cannot overwrite newer records; equal retrieval timestamps allow corrections. Partial batches never delete omitted records. Prior coverage reports remain, but superseded statistics are not kept as historical snapshots.

Both query functions return `{records, ingestions}`. Every record includes its `ingestion_id`; the corresponding report describes the original import batch, not a newly verified filtered result. Player history supports inclusive `start_date`/`end_date`, `limit` (1–1000), and `offset`; order is chronological. Unknown keys return empty lists. Query functions calculate no scores.

Database operations raise `DatabaseValidationError` for bad inputs or stale imports, `SchemaVersionError` for unsupported/unrecognized schema versions, and `sqlite3.Error` for storage failures. Callers close connections explicitly; ingestion owns its transaction and refuses an existing caller transaction. No database file has been populated with live season data during Phase 3; validation used temporary fixtures. Generated databases are ignored by Git.

## Evaluate a retrieved appearance

```python
from nba_mcp.scoring import evaluate_game_performance

if performance["records"]:
    evaluation = evaluate_game_performance(performance["records"][0])
    print(f"Game Score: {evaluation['game_score']:.1f}")
    print("Plus-minus:", evaluation["plus_minus"])
```

`calculate_game_score(record)` returns just a float; `evaluate_game_performance(record)` also returns separate plus-minus and formula provenance. Both are pure functions. Keep the original `performance["ingestions"]` coverage report alongside the evaluation: calculating a score does not certify source quality. Required counts must be normalized nonnegative integers; missing/null statistics raise `ScoringValidationError`. Negative scores and zero-stat games are valid. No extra shooting term or adjustable weight is accepted. See the dictionary for the full formula and contract.

## Run the local MCP server

First retrieve and import a dataset using the Python instructions above. Then, with the virtual environment active:

```sh
nba-mcp-server --database /absolute/path/to/nba.sqlite3
```

Equivalent: `python -m nba_mcp.server --database /absolute/path/to/nba.sqlite3`. The server communicates over stdio; an MCP client normally starts this process and sends requests. It has no web URL or HTTP listener. Stdout carries MCP messages; logs go to stderr. The explicit local database path is owner configuration and cannot be supplied through tool arguments. No database is created or imported on startup or query. Missing storage produces a tool error; an initialized empty database returns empty histories.

For clients that use the `mcpServers` JSON configuration convention, adapt these absolute paths:

```json
{
  "mcpServers": {
    "nba": {
      "command": "/absolute/path/to/nba-mcp-server/.venv/bin/python",
      "args": ["-m", "nba_mcp.server", "--database", "/absolute/path/to/nba.sqlite3"]
    }
  }
}
```

| MCP tool | Inputs | Result |
| --- | --- | --- |
| `get_player_games` | `player_id`; optional inclusive `start_date`, `end_date`, `limit` (1–1000, default 100), `offset` (default 0) | Chronological source statistics with original ingestion reports; empty histories succeed |
| `get_game_statistics` | `game_id`, `player_id` | One source appearance and its ingestion report; unknown key returns NOT_FOUND |
| `evaluate_game_performance` | `game_id`, `player_id` | Same appearance plus `evaluation` containing Game Score, separate plus-minus, and formula provenance |

IDs must be strings: player ID is positive numeric text; target-season game ID is 10 digits starting `00225`. Tools accept no names, raw statistics, weights, database paths, or refresh commands. All tool functions have explanatory docstrings exposed through discovery.

Successful structured results are `{ok: true, data: {records, ingestions}}`. Coverage findings remain visible even when `checks_passed` is false. A calculated score never changes `verified_complete`. Application failures return `{ok: false, error: {code, message}}` and set the MCP `isError` flag. Codes are `INVALID_ARGUMENT`, `NOT_FOUND`, `DATABASE_UNAVAILABLE`, `UNSUPPORTED_DATABASE`, `INVALID_DATABASE`, and `INVALID_STATISTICS`. Framework schema errors (missing/unknown arguments or wrong types) use native MCP errors rather than that application envelope. File paths and SQL details are omitted from storage error messages.

The adapter creates and closes a read-only SQLite connection inside each tool call. Retrieval/import remains an explicit Python operation. Phase 6 populated the ignored local `nba.sqlite3` file with 26,651 appearances for 582 players, covering 1,230 games. This file is local data, not part of the repository source. No desktop-client configuration was modified.

## Validation and refresh

Offline verification (no NBA requests):

```sh
python -m unittest discover -s tests -v
```

Explicit live verification/refresh (makes three NBA requests, writes the chosen database, starts a temporary local stdio server, and writes evidence):

```sh
python tests/live_validate.py --database nba.sqlite3 --report docs/phase6_live_report.json --timeout 15
```

Run normally, without Python's `-O` option: this test utility uses assertions. The timeout applies per upstream request, and live retrieval requests no automatic retries. Retrieval/normalization failure occurs before ingestion and leaves a prior dataset untouched. Ingestion is transactional; failed validation does not leave partial rows. Network-dependent checks are opt-in and excluded from unittest discovery. The report is written only after execution checks pass; inspect its timestamp so an earlier report is not mistaken for a new run. A successful script exit means execution checks passed, not that every source reconciliation passed. Inspect `coverage` and `player_summary_check`.

The October 16, 2026 run found every player/appearance count and additive statistic in agreement with the separate player-season summary endpoint. All 26,651 Game Scores matched a Decimal reference calculation, re-import reused its ingestion report, and six live-backed evaluations plus history/pagination/error workflows passed over real stdio. Team-log reconciliation still reported Houston FGA 86 from players versus 85 from the team log for game `0022500792`. No value was corrected or suppressed. `verified_complete` remains false because endpoints share a provider and that discrepancy remains unresolved.

Machine-readable evidence is in `docs/phase6_live_report.json`; human-readable scenarios, operational limits, and release recommendation are in `docs/WORK_PLAN.md`. Scripted MCP scenarios validate tool use; they are not a separate autonomous LLM benchmark or desktop-client acceptance test. The local database is ready for owner evaluation with those qualifications; no public release/deployment has been performed.

## Repository guide

| File or directory | Responsibility |
| --- | --- |
| `AGENTS.md` | Development rules and owner approval gates |
| `pyproject.toml` | Package metadata and build configuration |
| `src/nba_mcp/__init__.py` | Package marker |
| `src/nba_mcp/server.py` | Read-only FastMCP adapters, error mapping, and stdio entry point |
| `src/nba_mcp/nba_data.py` | NBA retrieval, normalization, and coverage reconciliation |
| `src/nba_mcp/database.py` | Versioned schema, atomic ingestion, and queries with coverage provenance |
| `src/nba_mcp/scoring.py` | Pure Game Score calculation and evaluation with separate plus-minus |
| `tests/test_project_setup.py` | Structure, configuration, and later-phase scope checks |
| `tests/test_nba_data.py` | Offline normalization, request, and coverage tests |
| `tests/test_database.py` | Temporary-file persistence, corrections, rollback, and query tests |
| `tests/test_scoring.py` | Hand-calculated examples, validation, and plus-minus independence |
| `tests/test_server.py` | MCP contracts, errors, read-only behavior, and real stdio smoke test |
| `tests/test_integration.py` | Offline endpoint-to-storage-to-stdio workflows, corrections, and failed refreshes |
| `tests/live_validate.py` | Opt-in live dataset refresh, cross-endpoint checks, score references, and stdio evidence |
| `tests/test_live_validation.py` | Offline validation of live-check helpers |
| `docs/phase6_live_report.json` | Timestamped live evidence and source-backed examples; no full dataset |
| `tests/fixtures/player_game_logs.json` | Synthetic source-shaped records, including a traded player |
| `docs/ARCHITECTURE.md` | Component boundaries and proposed data flow |
| `docs/DATA_DICTIONARY.md` | Proposed fields, units, sources, and metrics |
| `docs/WORK_PLAN.md` | Six phases, acceptance criteria, and progress |

Consult the work plan before starting any implementation phase.
