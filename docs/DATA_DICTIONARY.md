# Proposed data dictionary

Phase 2 implements the normalized fields below and verified them against live NBA Stats responses. Phase 3 persists those fields using the listed SQLite affinities in schema version 1. Target: `2025-26`, NBA regular season, one row per player appearance. Required statistics never silently default to zero.

## Identity and provenance

| Field | Type | Definition / proposed source |
| --- | --- | --- |
| `player_id` | str (TEXT) | Stable NBA player identifier; NBA player game-log data |
| `player_name` | str (TEXT) | Display name from NBA data; not a unique key |
| `game_id` | str (TEXT) | NBA game identifier, preserving leading zeros |
| `game_date` | str (TEXT) | Source game date normalized to ISO `YYYY-MM-DD`; not a timestamp |
| `season` | str (TEXT) | NBA season key, initially `2025-26` |
| `season_type` | str (TEXT) | Initially `Regular Season` |
| `team_id` | str (TEXT) | Team represented for this appearance |
| `team_abbreviation` | str (TEXT) | Source display abbreviation for that team |
| `opponent_team_id` | str or None (TEXT) | Currently always None: not supplied directly by the chosen player endpoint; no abbreviation-based inference |
| `matchup` | str (TEXT) | Source matchup description; parsing rules deferred |
| `result` | str (TEXT) | Completed-game result, validated W or L |
| `source` | str (TEXT) | Source dataset/endpoint identifier and provider |
| `retrieved_at` | str (TEXT) | UTC ISO 8601 normalization time immediately after retrieval; common to the batch |

## Game statistics

Source: NBA Stats `playergamelogs`, accessed through `nba_api==1.11.4`. Upstream fields below were observed in the live response. Counts are per game, not averages or percentages. Extra source fields (ranks, fantasy metrics, flags) are intentionally ignored.

| Field | Verified source field | Type | Definition |
| --- | --- | --- | --- |
| `minutes` | MIN | float (REAL) | Minutes played; normalize source time representation |
| `points` | PTS | int (INTEGER) | Points scored |
| `field_goals_made` | FGM | int (INTEGER) | Made field goals, including three-pointers |
| `field_goals_attempted` | FGA | int (INTEGER) | Attempted field goals |
| `three_pointers_made` | FG3M | int (INTEGER) | Made three-point field goals |
| `three_pointers_attempted` | FG3A | int (INTEGER) | Attempted three-point field goals |
| `free_throws_made` | FTM | int (INTEGER) | Made free throws |
| `free_throws_attempted` | FTA | int (INTEGER) | Attempted free throws |
| `offensive_rebounds` | OREB | int (INTEGER) | Offensive rebounds |
| `defensive_rebounds` | DREB | int (INTEGER) | Defensive rebounds |
| `total_rebounds` | REB | int (INTEGER) | Total rebounds; validate against offensive + defensive |
| `assists` | AST | int (INTEGER) | Assists |
| `steals` | STL | int (INTEGER) | Steals |
| `blocks` | BLK | int (INTEGER) | Blocked shots |
| `turnovers` | TOV | int (INTEGER) | Turnovers |
| `personal_fouls` | PF | int (INTEGER) | Personal fouls |
| `plus_minus` | PLUS_MINUS | int (INTEGER) | Team point differential while the player is on court; signed |

Validation checks nonnegative integer counts, made <= attempted, three-pointers <= total field goals, points consistent with made shots, consistent rebounds, and preserved signed plus-minus. Numeric whole-valued strings/floats are accepted; booleans, nonfinite numbers, fractional counts, and missing statistics are rejected. Minutes accept finite nonnegative numeric values or `MM:SS` with seconds below 60. Zero minutes are retained; null/DNP markers fail rather than become zero or silently disappear. The live season returned two zero-minute appearances; the full population of possible upstream DNP representations is not established.

Identity mappings: `PLAYER_ID`, `PLAYER_NAME`, `TEAM_ID`, `TEAM_ABBREVIATION`, `GAME_ID`, `GAME_DATE`, `MATCHUP`, and `WL`. `SEASON_YEAR` must equal `2025-26`. Game IDs must be 10-digit strings starting `00225`; postseason/preseason/Cup championship IDs are rejected. Dates accept ISO source date/timestamps and normalize to date strings within July 2025–June 2026. These are broad year bounds; game ID and source season are the principal season guards. Team reference rows require `SEASON_ID=22025`. Duplicate `(game_id, player_id)` or reference `(game_id, team_id)` keys fail validation.

`season_type` is the explicit request context. `source` includes provider, endpoint, and client version. `retrieved_at` represents this retrieval, not the provider's last correction time. `matchup` is preserved without parsing. Players without appearances are outside this dataset.

## Coverage report

`fetch_season_dataset` returns `records` and `coverage`. Coverage contains string `season`, `season_type`, and `reference_source`; integer `player_count`, `appearance_count`, `game_count`, and `team_game_count`; boolean `checks_passed` and `verified_complete`; and a string list `issues`. `checks_passed` means the documented reconciliation checks agree. `verified_complete` is always false: these aggregate/provider checks do not independently prove that every zero-stat appearance is present.

The October 14, 2026 live check returned one issue: Houston in game `0022500792` had summed player FGA 86 and team-log FGA 85. Source values are preserved and the report fails reconciliation; no correction is inferred. A separate V3 box-score request returned no usable normalized datasets, so the discrepancy remains unresolved.

Phase 6 repeated live verification on October 16, 2026 and observed the same team-log issue. The separate player-season endpoint agreed on all 582 player IDs, games-played counts (26,651 appearances in total), and additive statistics, including signed plus-minus. This supports appearance coverage but does not resolve the team-log FGA disagreement or certify independent ground truth. Ingestion retains `checks_passed=False` and `verified_complete=False`; neither is silently promoted by successful application tests.

## SQLite schema version 1

`PRAGMA user_version` is 1. Tables are created only in an empty unversioned database. No migration or deletion policy is implemented.

`player_games` contains all normalized record fields above. The primary key is `(game_id, player_id)`. All fields are NOT NULL except `opponent_team_id`; no missing statistic has a zero default. `ingestion_id INTEGER NOT NULL` references `ingestions.ingestion_id`. IDs remain TEXT, preserving leading zeros in game IDs. A composite index on `(player_id, game_date, game_id)` supports histories; the primary-key index supports individual appearance lookups. Names and teams are per appearance, so traded-player history is not overwritten by a season-wide team value.

| `ingestions` field | SQLite type | Definition |
| --- | --- | --- |
| `ingestion_id` | INTEGER PRIMARY KEY | Local ingestion-report identifier; not an NBA ID |
| `fingerprint` | TEXT NOT NULL UNIQUE | SHA-256 of canonical records plus coverage, including retrieval timestamps |
| `imported_at` | TEXT NOT NULL | UTC ISO 8601 timestamp of initial local insertion of this report |
| `coverage_json` | TEXT NOT NULL | Full Phase 2 coverage dictionary encoded with Python JSON; no SQLite JSON extension required |

SQLite enforces the appearance key, report foreign key, fixed season/type, game ID shape, completed result, nonnegative integer counts, signed integer plus-minus, nonnegative numeric minutes, shot inequalities, rebound sums, and point arithmetic. Python validates the stricter normalized contract before writing: exact fields, canonical positive player/team IDs, calendar dates, aware UTC timestamps, finite numeric minutes, signed 64-bit integer bounds, unique batch keys, and report shape/count consistency. Normalized counts must already be Python integers (booleans/strings are rejected). The database module does not repeat upstream coercion or infer missing inputs.

Ingestion permits both failing and passing coverage reports and always requires `verified_complete=False` for the current Phase 2 contract. `appearance_count` and `player_count` must describe the batch; reference game/team counts remain the report's source facts even when they differ from appearances. The report is trusted as supplied, not independently recalculated from team logs at storage time. Invalid report structure or contradictory `checks_passed`/`issues` rejects the batch.

Re-importing an identical batch reuses `ingestion_id`; changing input order has no effect. A new timestamp or corrected value creates a new report and upserts current appearances atomically. Older retrieval timestamps reject the entire import; equal UTC instants allow correction. Omitted records are retained. Prior reports remain queryable through SQL, but superseded appearance values are overwritten; the fingerprint is an identity check, not a stored snapshot of those old records.

Queries return `{records, ingestions}`. Record dictionaries reproduce the normalized fields and add `ingestion_id`. Each ingestion dictionary contains `ingestion_id`, `imported_at`, and decoded `coverage`. Coverage refers to the original batch, not a filtered page or current database-wide completeness assessment. Unknown player/game keys return empty lists. Dates are inclusive, ordering is ascending date/game/player, default page size is 100, maximum is 1000, and offset defaults to zero.

## Derived metrics

Phase 4 implements the owner-approved **unmodified Hollinger Game Score**, with plus-minus displayed separately as context. This replaces the previous custom-score proposals. No true-shooting term, separate adjustment coefficient, or caller-supplied weight is included.

### Game Score

```text
game_score = points + 0.4 * field_goals_made - 0.7 * field_goals_attempted
             - 0.4 * (free_throws_attempted - free_throws_made)
             + 0.7 * offensive_rebounds + 0.3 * defensive_rebounds
             + steals + 0.7 * assists + 0.7 * blocks
             - 0.4 * personal_fouls - turnovers
```

The existing free-throw terms remain. Three-pointers and made free throws contribute through total points. `plus_minus` is not an input to this calculation and must not change `game_score`.

| Evaluation output field | Type | Meaning |
| --- | --- | --- |
| `game_score` | float | Unmodified Hollinger Game Score; may be negative |
| `plus_minus` | int | Existing signed source statistic, shown separately; not a derived adjustment |
| `formula_version` | str | `hollinger-game-score-v1`: identifies the project's implementation of the original formula |
| `formula_source` | str | `https://www.basketball-reference.com/about/glossary.html` |

Formula verified October 14, 2026 against the [Basketball Reference glossary](https://www.basketball-reference.com/about/glossary.html) and the [publisher's glossary](https://faq.sports-reference.com/portal/en/kb/articles/basketball-glossary), attributed to John Hollinger. The version identifier is local provenance, not a publisher-assigned version.

`calculate_game_score` requires the 12 count fields appearing in the equation. Each must be a Python integer (not bool), nonnegative and below 2^63; strings, floats, nulls, missing fields, and made shots exceeding attempts raise `ScoringValidationError`. Additional fields are ignored. Cross-field source checks involving fields outside the equation (such as three-pointer counts or total rebounds) remain in normalization/storage; scoring does not certify a complete box score.

`evaluate_game_performance` additionally requires signed 64-bit integer `plus_minus` and returns the four fields above. The score-only function does not require or use plus-minus. Neither function copies identifiers or ingestion reports; callers keep those in the original query envelope. Neither mutates inputs or performs I/O.

All coefficients are exact tenths, so computation uses integer tenths and converts to a float only at the end. Display with one decimal place; no intermediate rounding, clamping, or adjustable precision is applied. All-zero inputs yield 0.0; negative scores are valid. Plus-minus is context-dependent and is not a standalone causal measure of player impact. Rankings, aggregation, and performance labels are outside Phase 4. No SQLite schema change or stored score is introduced.

Worked synthetic examples (not actual player performances): a 30-point game with FGM/FGA 10/20, FTM/FTA 8/10, OREB/DREB 2/6, STL 2, AST 7, BLK 1, PF 3, TOV 4 yields 24.8; eight made of ten free throws with other counts zero yields 7.2; four made threes on eight attempts with other counts zero yields 8.0. Five missed field goals, two missed free throws, four fouls, and three turnovers with other counts zero yields -8.9.

## MCP result contract (Phase 5)

Tools read only the configured `2025-26` database. `get_player_games` returns histories; `get_game_statistics` returns an individual appearance; `evaluate_game_performance` adds a nested `evaluation` to that individual record. The nested object has the four evaluation fields above. Raw statistics, identifiers, `ingestion_id`, and referenced reports are preserved. No SQLite columns or score-persistence policy changed.

Success: `{ok: true, data: {records: [...], ingestions: [...]}}`. Records and ingestion objects follow the existing query contract. An empty history returns two empty lists; an unknown individual appearance returns NOT_FOUND. `checks_passed` and `verified_complete` retain their source meanings. Callers must not treat a successful MCP request or calculated score as a completeness certificate.

Application failure: `{ok: false, error: {code: string, message: string}}`, with MCP `isError=true`. Codes:

| Code | Meaning |
| --- | --- |
| `INVALID_ARGUMENT` | Business-layer validation rejected an ID, date bound, or pagination value |
| `NOT_FOUND` | No appearance matches the individual game/player key |
| `DATABASE_UNAVAILABLE` | Configured storage is missing, unreadable, busy, or invalid; no empty file is created |
| `UNSUPPORTED_DATABASE` | Database schema version is unsupported |
| `INVALID_DATABASE` | Stored coverage JSON is malformed |
| `INVALID_STATISTICS` | Scoring rejected the stored formula inputs or plus-minus context |

Native framework errors cover missing/extra arguments and wrong JSON types before tool execution; these set the MCP error flag without guaranteeing the application error envelope. Unexpected errors are masked by FastMCP. Local file paths/SQL details are not returned in storage errors. Database path, season, formula, and weights are not tool arguments. Input IDs are strings, dates are optional ISO date strings, and page/offset are strict integers (booleans and numeric strings are rejected). Full input descriptions and pagination limits are in README and tool docstrings.

## Phase 6 evidence artifact

`docs/phase6_live_report.json` is a validation artifact, not a database table or an MCP response schema. It records UTC `validated_at`, the local database path and SHA-256, date range, original coverage, import/re-import result, database integrity, score-reference count, and stdio scenario evidence. SHA-256 describes that file at validation time; a subsequent import changes it.

`player_summary_check` reports a second NBA result set: player count, missing/unexpected player IDs, per-player GP/stat differences, `appearance_counts_match`, and `checks_passed`. A failed/unavailable summary request is explicitly recorded instead of treated as a pass. `mcp_check.samples` contains selected original statistics and actual returned evaluations; additional flags cover pagination/error behavior. `desktop_or_separate_llm_tested=False` distinguishes scripted MCP workflows from acceptance in a separate client or model harness.

The live report's `verified_complete` remains false. Its successful score comparisons and client scenarios describe software behavior, while coverage fields describe source quality; they are separate conclusions. No metric definition, coefficient, or SQLite schema was changed in Phase 6.
