# Proposed architecture

Phases 2–5 implement retrieval, normalization, coverage checks, SQLite persistence/queries, pure Game Score evaluation, and local read-only MCP adapters. Phase 6 validates the local full workflow with synthetic fixtures and a live season import. Separate desktop/LLM acceptance and release/deployment remain outside the completed local validation scope.

```text
AI Agent
    |
    v
MCP Server (server.py)
    |
    v
Python business functions
    |-- nba_data.py --> NBA API (via nba_api)
    |-- database.py --> SQLite (via sqlite3)
    `-- scoring.py  --> pure metric calculations
```

## Component responsibilities

| Component | Planned responsibility | Boundary |
| --- | --- | --- |
| `server.py` | Validate tool requests, call business functions, serialize responses/errors | No SQL, upstream parsing, or scoring formulas |
| `nba_data.py` | Retrieve the approved season/type, normalize upstream fields, validate rows and source metadata | No MCP transport code |
| `database.py` | Own schema, transactions, idempotent writes, and parameterized queries | No upstream network calls or scoring formulas |
| `scoring.py` | Compute unmodified Hollinger Game Score; plus-minus remains separate context | No network, database, or MCP side effects |

Business functions will be callable and testable without starting MCP. Their exact signatures and tool contracts will be designed in their respective approved phases; no orchestration module is needed now.

## Proposed data flow

1. An explicitly initiated ingestion operation requests `2025-26`, `Regular Season` from NBA data sources.
2. Retrieval normalizes identifiers, dates, numeric values, participation, and provenance. Invalid or missing required data is reported rather than silently converted to zero.
3. Persistence validates record keys and writes data transactionally. Repeated ingestion updates records without duplicating a player appearance.
4. An agent requests player statistics or an individual performance through an MCP tool.
5. The adapter invokes Python queries against SQLite and, when requested, pure scoring functions. Planned responses include source context, Game Score formula version, and plus-minus as a separate source statistic.

Prefer local reads for agent queries and explicit data refreshes. This reduces latency and repeated upstream requests, at the cost of needing freshness metadata and a refresh policy. The exact refresh mechanism is deferred for owner review; no scheduler is proposed in Phase 1.

## Decisions and trade-offs

- A single Python package keeps deployment and navigation simple. The `src` layout helps detect packaging mistakes instead of relying on root-directory imports.
- SQLite avoids running a separate database service and fits an initial local dataset. Concurrent writers and multi-host deployment would require reassessment if they become requirements.
- Store source statistics as the primary record; calculate Game Score on demand initially. This avoids stored scores becoming inconsistent with corrected source statistics, at the cost of small repeated calculations.
- Preserve game and player IDs as strings to retain formatting and avoid using ambiguous names as keys. Proposed appearance key: `(game_id, player_id)`; the Phase 2 live normalization found no duplicates for this key. Finalize its database constraint in Phase 3.
- Cover players with at least one regular-season appearance, including appearances for different teams after trades. Do not use roster membership as proof of appearance or treat team season totals as game rows.
- The owner selected unmodified Hollinger Game Score with plus-minus shown separately. Phase 4 implements that choice, superseding earlier custom-score proposals. Missing statistics fail explicitly, scores display to one decimal, and ranking remains out of scope. See the data dictionary for the contract.

## Deferred decisions

FastMCP 4.1.0 and local stdio are selected in Phase 5. No migration framework, authentication layer, hosting platform, or HTTP deployment is selected.

## Phase 4 implementation

`scoring.py` uses only `collections.abc.Mapping` from the standard library. `calculate_game_score(statistics)` validates the 12 required normalized counts and shot inequalities, then returns the original formula's float result. `_count` centralizes missing/type/range validation and raises `ScoringValidationError`. Integer tenths avoid intermediate floating-point arithmetic and preserve the formula's natural one-decimal resolution.

`evaluate_game_performance(statistics)` calls the same score function, validates signed plus-minus, and returns `game_score`, separate `plus_minus`, `formula_version`, and `formula_source`. It copies no input data and performs no I/O. Callers retain source identifiers and ingestion coverage from the original database query. This keeps scoring independently usable without a database or NBA client and avoids a misleading implication that a calculated score certifies the underlying dataset. No schema changes, score persistence, rankings, or labels were added. Hand-worked examples and limitations are in the data dictionary.

## Phase 2 implementation

`nba_data.py` uses `PlayerGameLogs` for league `00`, season `2025-26`, season type `Regular Season`, and `Totals`. No player/team/date restriction is applied. This avoids incomplete roster lists and per-player request loops. The endpoint returns a full result set with no pagination parameter exposed by the client. Coverage is checked separately rather than inferred from that interface.

| Function | Inputs and outputs | Purpose |
| --- | --- | --- |
| `fetch_player_game_logs` | Timeout/retry options → normalized appearance list | Requests and validates all player logs |
| `normalize_player_game_logs` | Upstream dictionary list → sorted normalized list | Enforces types, season, keys, and statistical relationships without network access |
| `reconcile_season_coverage` | Normalized appearances + raw team logs → coverage report | Compares game/team sets, dates, team schedules, minimum player counts, and shooting totals |
| `fetch_season_dataset` | Timeout/retry options → `{records, coverage}` | Retrieves player logs followed by `LeagueGameLog` team references; returns only after both succeed |
| `_request_rows` | Endpoint factory, dataset name, options → raw list | Centralizes request limits, retry policy, and response-shape errors |
| `_integer`, `_identifier`, `_text`, `_game_identity`, `_minutes` | Source values → validated scalar values | Keep conversion rules consistent and errors explicit |

`DataValidationError` rejects unusable rows, duplicate appearances, wrong-season data, or empty results. `NBADataError` wraps upstream transport/parser failures. No rows are silently dropped and no missing statistics become zero. Genuine zero-minute entries are retained because rounding can hide brief appearances; DNP/null minutes are rejected for investigation. Name changes do not change player identity and trades preserve each appearance's team.

Defaults are a 30-second requests timeout and one retry. Connection failures and timeouts receive 0.5-second then (if two retries are requested) 1-second backoff. HTTP/parser/data errors are not automatically retried; callers must inspect failures rather than repeatedly hitting an unavailable service. Each endpoint uses the client's normal headers, no proxy evasion or parallel requests. Sequential retrieval reduces upstream load but takes longer than parallel retrieval. Rate limits are not contractually established by these tests.

Coverage expects 1,230 games, 30 teams with 82 games each, two teams per game, at least five player appearances per team-game, matching dates and game/team sets, and matching player sums for points and shooting counts. Rebounds and turnovers are not additive reference checks because team-only events can exist. The two endpoints share a provider; checks do not independently prove every appearance. In particular, a missing zero-stat player can escape aggregate checks. `verified_complete` remains false, even when `checks_passed` is true.

Reference sources: [nba_api PlayerGameLogs source](https://github.com/swar/nba_api/blob/v1.11.4/src/nba_api/stats/endpoints/playergamelogs.py), [LeagueGameLog source](https://github.com/swar/nba_api/blob/v1.11.4/src/nba_api/stats/endpoints/leaguegamelog.py), and the [package release](https://pypi.org/project/nba_api/1.11.4/). Installed 1.11.4 signatures and actual NBA responses were also checked; documentation alone is not treated as live validation.

## Phase 3 implementation

`database.py` imports only the standard library. It consumes `{records, coverage}` from Phase 2 but never fetches or repairs source data. Its validation requires normalized types, unlike the retrieval layer, which converts upstream values. Keeping that storage contract explicit catches malformed imports without making database access depend on NBA client imports.

Two tables keep this local design simple: `player_games` contains the latest version of each appearance and points to an `ingestions` report. Per-appearance names and teams preserve source context across trades and name changes. Separate player/team dimension tables would add synchronization work without a current query need. The data dictionary documents schema version 1.

| Function / exception | Responsibility |
| --- | --- |
| `open_database(path)` | Open/create a file or `:memory:`, enable foreign keys, initialize empty schema, check version, return a connection the caller closes |
| `ingest_dataset(connection, dataset)` | Validate all input, fingerprint the batch, and atomically upsert appearances with their report; return ingestion ID, processed count, and whether the report is new |
| `get_player_games(...)` | Read chronological history with inclusive dates and bounded pagination; include relevant ingestion reports |
| `get_game_performance(...)` | Read a single player's game statistics and its ingestion report; no scoring |
| `_validate_dataset` | Enforce normalized fields, statistics, supported season, unique keys, report shape, and matching appearance/player counts |
| `_transaction` | Own `BEGIN IMMEDIATE`/commit/rollback; refuse a caller-owned transaction |
| `_query_result` | Join records and provenance in a single consistent SQL read and build the shared response envelope |
| `_identifier`, `_iso_date`, `_timestamp` | Validate canonical ID/date/UTC inputs and support freshness checks |
| `DatabaseValidationError` | Report malformed input, stale imports, or transaction ownership errors |
| `SchemaVersionError` | Reject an unsupported version or unrelated unversioned database rather than changing it |

Writes use bound parameters and `ON CONFLICT(game_id, player_id) DO UPDATE`. SQL identifiers come only from fixed module constants. An explicit transaction covers both the report and every row; an invalid/stale row or storage error leaves neither partial rows nor an orphan report. Python validation runs before writes, while SQLite also enforces keys, foreign keys, required values, count types, season restrictions, and statistical relationships. A five-second SQLite lock timeout permits brief writer contention; the design remains one local writer at a time, without WAL tuning or a connection pool.

SHA-256 over canonical JSON (sorted record order and dictionary keys, including timestamps and coverage) deduplicates identical batch reports. Reordered records reuse the report. A fresh timestamp or correction creates a new report. An existing report is still subject to per-row freshness checks on re-import. Equal timestamps permit explicit corrections; older timestamps reject the whole batch. Freshness compares UTC instants, not string formatting. Imports never delete omitted records, so a partial refresh cannot erase other appearances; source deletions require a future explicit policy.

Queries return `records` and deduplicated `ingestions`. Each record carries an `ingestion_id`; each relevant ingestion includes its timestamp and full original coverage report. Multiple reports may be represented after partial updates. A report describes its input batch, not the current union of all stored rows or a filtered query. Storage validates the report structure and key counts but trusts its reconciliation findings; it cannot independently recheck totals without the raw team logs. Failed reconciliation is allowed and remains visible; it never changes source statistics or `verified_complete=False`.

`PRAGMA user_version=1` records the schema. Initialization refuses other nonzero versions and unrelated unversioned tables; no migrations are implemented. A future schema change must receive approval and explicit migration work. SQLite 3.24+ is required for UPSERT. Connections use autocommit outside explicit write operations and must be closed by callers (`contextlib.closing` in README). No global connection, filesystem location, network call, or import side effect is introduced.

Trade-off: previous ingestion reports are retained, but the database is not a historical archive of every statistics revision. This keeps queries and disk usage simple; full revision history would need a separate approved requirement. Local database backups are the owner's responsibility. No live season import, hosting, or scheduler was added in Phase 3.

## Phase 5 implementation

`server.py` registers three read-only tools on an owner-configured FastMCP instance. `get_player_games` delegates to the database history query; `get_game_statistics` delegates to the individual appearance query; `evaluate_game_performance` uses the same query and delegates calculations to `scoring.py`. Adapters contain no SQL, NBA requests, or score arithmetic. All source statistics and referenced ingestion reports remain in returned envelopes; evaluation adds a nested object rather than replacing the source row.

| Function | Responsibility |
| --- | --- |
| `create_server(database_path)` | Resolve one configured local path and register the three tools with instructions, schemas, strict input validation, and read-only annotations |
| `player_history`, `game_statistics`, `game_evaluation` | Documented nested tool adapters exposed under the public MCP names in README; call existing business functions |
| `_query` | Own a per-call read-only connection, delegate the query/evaluation, preserve provenance, and translate expected errors |
| `_error` | Build stable structured application failures with MCP's error flag |
| `main` | Parse required `--database` and run stdio without a banner; supports module and console entry points |
| `database.open_readonly_database` | Open an existing file with SQLite URI `mode=ro`, validate version/required columns, and return a connection the caller closes; never initialize missing storage |

FastMCP dispatches synchronous tools to worker threads. Each connection is created, used, and closed within that same call/thread, avoiding SQLite cross-thread sharing. A single SELECT joins source records to reports for a consistent query snapshot. Initialization/import uses the existing separate Python API; the MCP layer cannot mutate records, choose files, fetch data, or modify scoring policy. File URIs escape spaces and reserved characters. SQLite read-only mode enforces the restriction in addition to MCP annotations (annotations alone would not enforce it).

Stdio is the initial transport because this is a local database service: no HTTP listener, hosting, authentication configuration, or public endpoint is needed. The console entry point `nba-mcp-server` and module invocation share `main`. No server is constructed or started at module import. The process starts on client demand; application code does not print to protocol stdout. FastMCP logs to stderr.

Success envelopes contain `ok` and the original `{records, ingestions}` data. Application failures carry stable code/message fields and set `isError`. Native framework schema failures remain protocol errors; unexpected internal details are masked. Known database errors omit paths/SQL text. The data dictionary documents error codes and evaluation additions. Empty histories succeed; missing individual appearances are explicit NOT_FOUND errors. A report describes its original batch, not a fresh assertion about query completeness.

Pinning FastMCP 4.1.0 fixes the tested interface; its package metadata supports Python 3.10+ and is compatible with the project's Python 3.11+ requirement. It adds transitive dependencies and requires later update testing; transitive versions are not locked. Installed signatures, tool contracts, and subprocess stdio were tested with Python 3.12. References: [FastMCP tools](https://gofastmcp.com/servers/tools), [client transports](https://gofastmcp.com/clients/transports), and [release metadata](https://pypi.org/project/fastmcp/4.1.0/). No desktop client configuration was modified in this phase.

## Phase 6 verification design

The application architecture, formula, schema, and three-tool scope are unchanged. Test utilities coordinate the existing components rather than introducing a new runtime service or framework.

`tests/test_integration.py` mocks only NBA endpoint constructors. `retrieve` passes those responses through production normalization and coverage reconciliation; `client` starts a real stdio subprocess against a temporary persisted file. Tests cover repeated imports, a corrected assist/plus-minus value after reopening storage, a brief zero-stat appearance after a trade, and continued access to the last import after a malformed/failed refresh. Every scenario retains source coverage instead of treating successful tool calls as proof of completeness.

The opt-in `tests/live_validate.py` has these functions:

| Function | Responsibility |
| --- | --- |
| `compare_player_totals` | Compare player sets, games played, and additive counts against separate `LeagueDashPlayerStats` totals; reject duplicate/ambiguous reference rows |
| `reference_game_score` | Evaluate published coefficients with Decimal arithmetic to cross-check the application's integer-tenths implementation |
| `check_stdio` | Start the actual MCP module and verify selected live-backed evaluations, complete history for a selected player, pagination, errors, and provenance |
| `validate` | Fetch the season, run cross-endpoint checks, ingest transactionally, verify repeatability/integrity/all scores, and assert MCP does not change the database |
| `main` | Require explicit database/report destinations, run verification, and write the timestamped report after successful execution |

Live source checks use the existing player/team endpoints and one additional read-only summary request in test tooling. No new production dependency or MCP refresh capability is added. This provides stronger evidence against missing zero-stat appearances because per-player games played are checked, but a shared provider is not an independent ground-truth source. Source disagreement remains visible in the report and does not result in fabricated corrections or `verified_complete=True`.

The local database is ignored by Git, including SQLite journal/WAL sidecars. Report JSON contains selected records and audit results, not the full season dataset. Re-running the live utility refreshes the explicit file according to existing newer-timestamp rules; omitted-source-record deletion remains unsupported. Use the default unoptimized Python mode for this assertion-based validation utility. Execution failure stops the check; a previous report remains timestamped and must not be confused with fresh evidence. Successful execution can coexist with recorded source-quality failures.
