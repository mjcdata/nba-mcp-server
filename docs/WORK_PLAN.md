# Six-phase work plan

## Current progress

All six phases have completed their local implementation/validation scope. The owner approved Phase 6 on October 16, 2026. The ignored local `nba.sqlite3` now holds 26,651 appearances for 582 players, covering 1,230 games. The Houston source discrepancy remains unresolved; source correctness/completeness is not certified. Release/deployment and separate desktop/LLM acceptance are not approved or claimed.

| Phase | Tracker |
| --- | --- |
| 1. Project Setup and Documentation | Complete |
| 2. NBA API Integration | Complete; live source quality qualified |
| 3. SQLite Database Implementation | Complete |
| 4. Performance Scoring Implementation | Complete |
| 5. MCP Server Implementation | Complete |
| 6. Integration and Agent Testing | Local validation complete; limitations documented |

Phase 6 steps: [x] review; [x] offline full-path tests; [x] live retrieval/import/reference checks; [x] real stdio scenarios; [x] evidence, operational guidance, and release recommendation. Begin future responses with this phase/step tracker as requested by the owner.

## Phase 1 — Project Setup and Documentation

- Objective: establish the approved structure and a shared implementation contract.
- Deliverables: package configuration; README and AGENTS; architecture, dictionary, and this plan; docstring-only modules; offline setup tests.
- Acceptance: structure and documentation exist, configuration is valid, packaging discovers `nba_mcp`, and later-phase behavior and unnecessary dependencies are absent.
- Tests: parse TOML, verify required files and metadata, confirm placeholders have no executable functionality, validate package discovery/build, and run unittest discovery.
- Status: complete; see validation record below. Stop after the completion report.

## Phase 2 — NBA API Integration

- Objective: retrieve and normalize every player appearance for the 2025–2026 regular season.
- Deliverables: `nba_data.py` retrieval functions, approved `nba_api` dependency, source mappings, representative fixtures, documented coverage and network-error behavior.
- Acceptance: season/type filtering, identifiers, traded players, participation, nulls, numeric conversions, and duplicate detection are verified. A documented reconciliation checks season coverage rather than assuming the returned set is complete.
- Tests: offline normalization and mocked retrieval tests; separately reported live sample/coverage checks when feasible. Document upstream access failures and retry limits.
- Gate: explicit owner approval required before starting; review source/schema findings before Phase 3.
- Status: owner approval received; implementation complete with the live data limitation documented below. Do not certify the dataset as reconciled or complete.

## Phase 3 — SQLite Database Implementation

- Objective: persist normalized data and provide reliable Python queries.
- Deliverables: approved schema, `database.py`, transactional/idempotent ingestion, parameterized queries, initialization instructions, updated dictionary.
- Acceptance: validated keys prevent duplicate appearances, re-ingestion handles corrections, trade history is retained, and rollback/query behavior is documented.
- Tests: temporary SQLite databases covering round trips, repeated imports, invalid rows, rollback, and query boundaries.
- Gate: explicit owner approval required before starting.
- Status: owner approval received; implementation and offline validation complete. No live-season database was populated during Phase 3; Phase 6 subsequently populated it.

## Phase 4 — Performance Scoring Implementation

- Objective: evaluate individual games with unmodified Hollinger Game Score and show plus-minus separately as context.
- Deliverables: pure `scoring.py` functions; verified formula reference; missing-data policy, precision policy, formula version, and documented examples. No true-shooting adjustment or plus-minus weight is planned.
- Acceptance: scores reproduce the original Game Score formula and are unaffected by plus-minus; callers supply no weights; no silent zero substitution for missing statistics.
- Tests: independently hand-calculated examples, negative scores, zero-stat games, invalid/missing inputs, and invariance of Game Score to plus-minus changes.
- Gate: explicit owner approval required before starting; approve scoring choices before implementation.
- Status: owner approved the original formula and then explicitly authorized Phase 4. Implementation and offline validation are complete; see the validation record below.

## Phase 5 — MCP Server Implementation

- Objective: expose approved Python operations to AI agents.
- Deliverables: thin FastMCP adapters in `server.py`, approved dependency/version and transport, tool schemas, structured errors and startup instructions.
- Acceptance: tools call reusable business functions, inputs are validated, results include provenance, and transport code contains no scoring or SQL logic.
- Tests: adapter/contract tests and a local MCP client smoke test, including malformed inputs and unavailable data.
- Gate: explicit owner approval required before starting.
- Status: owner approved Phase 5; adapters and local client smoke validation are complete. FastMCP 4.1.0 uses stdio; no HTTP deployment, live refresh tool, or client-app configuration was added.

## Phase 6 — Integration and Agent Testing

- Objective: verify the full retrieval/storage/scoring/tool workflow and agent usability.
- Deliverables: integration tests, agent scenarios, completeness evidence, operational instructions, known limitations, and release recommendation.
- Acceptance: a reproducible end-to-end run, independently verified scoring examples, documented coverage/freshness, useful agent responses, and clear failure behavior.
- Tests: offline full-path fixtures, database repeatability, MCP client scenarios, and separately reported live verification. Do not declare completeness when upstream checks are blocked.
- Gate: explicit owner approval required before starting; release/deployment decisions require owner approval.
- Status: owner approval received October 16, 2026; local integration and scripted agent-use validation complete. Live source consistency remains qualified; desktop/independent-LLM acceptance was not performed.

## Phase 1 validation record

Validated on October 14, 2026 with the bundled Python 3.12 runtime:

- `python -m unittest discover -s tests -v`: **3 tests passed** (required files, TOML configuration, docstring-only module scope).
- Setuptools discovery returned exactly `nba_mcp`.
- `setuptools.build_meta.build_wheel` in a temporary copy: **passed**, producing `nba_mcp_server-0.1.0-py3-none-any.whl`. Archive inspection confirmed all five package modules were included.
- No network calls or later-phase implementations were used.

The system Git/Python launchers require unavailable Apple developer tools; validation used the available bundled Python instead. The directory was initially empty and no Git repository was initialized. The build used the installed backend without dependency isolation; a clean-environment dependency download/editable install was not tested. Python 3.11 compatibility is configured but was not separately runtime-tested.

## Recommended next action

Repository follow-up: the earlier `mjcdata/nba-mcp-repo` URL returned 404. On October 16, 2026 the owner authorized publishing the completed work to `mjcdata/nba-mcp-server`. That repository and its `main` branch were verified; its initial commit contains only a README. The source publication preserves that history and includes code, tests, documentation, and selected validation evidence. Generated SQLite files, environments, and caches remain local. This publication authorization does not authorize deployment or changing the scoring formula.

Recommend owner evaluation of the local server with the populated database and visible quality findings. Then choose a desktop MCP client for explicit acceptance before release. This tests real usability beyond scripted calls but requires client-specific configuration. Track the Houston source discrepancy separately; do not alter source values without evidence. No seventh phase is assumed and no release/deployment, client configuration, commit, or push is authorized by completing Phase 6.

## Phase 2 validation record

Validated on October 14, 2026 with Python 3.12 and `nba_api==1.11.4`:

- Created the local `.venv` and successfully installed the project with `pip install -e .`, including isolated setuptools build setup. A preliminary non-isolated install lacked setuptools; the normal documented install succeeded.
- `pip check`: no broken requirements.
- `python -m unittest discover -s tests -v`: **19 tests passed**. Offline tests cover fixed request parameters, bounded timeout/connection retries, parser/HTTP failures, malformed/empty responses, invalid request options, normalization, trades, brief/zero-minute appearances, missing values, duplicate keys, season guards, and coverage differences. A synthetic full-season set exercises 30 teams, 82 games each, and minimum appearance checks. Fixtures are synthetic, not assertions of actual player performance.
- Live `fetch_season_dataset(timeout=20, retries=0)` returned **26,651 appearances**, **582 players**, **1,230 games**, and **2,460 team-game references**, dated **2025-10-21 through 2026-04-12**. There were **72 players representing multiple teams** and **2 zero-minute rows**. Raw/normalized datasets were held in memory only, with no persistent dataset created in this phase.
- Reconciliation found **one mismatch**: game `0022500792`, Houston (`1610612745`), summed player FGA **86** versus team-log FGA **85**. All other initially checked game/team/date/shooting comparisons agreed. Additional team-schedule and minimum-appearance checks are also tested offline.
- Investigative `BoxScoreTraditionalV3` request for that game returned an empty normalized dictionary. It provided no usable evidence to resolve the discrepancy; this diagnostic is not part of the implemented retrieval path.
- Result: retrieval and offline behavior validated; source consistency remains qualified. Neither a passing aggregate report nor this live run proves every zero-stat appearance is present. Independent per-player/box-score completeness verification remains a Phase 6 concern.

Runtime dependencies are limited to nba_api directly; its release requires NumPy, pandas, and requests transitively. No FastMCP, database framework, CLI framework, or scheduler was introduced. Python 3.11 is supported by project metadata but not separately runtime-tested. No commit or push was made.

## Phase 3 validation record

Validated on October 14, 2026 with Python 3.12.14 and SQLite 3.53.1:

- `python -m unittest discover -s tests -v`: **35 tests passed**, including 16 new temporary-file database tests and all existing retrieval/setup tests.
- Verified source-field round trips, close/reopen persistence, signed plus-minus, nullable opponent, team changes, repeated/reordered ingestion, corrections, retained reports, partial refreshes, stale-input protection, date boundaries, pagination, unknown keys, malformed inputs, report structure, and SQL parameter safety.
- A forced SQLite trigger failure on the second appearance rolled back both the first inserted row and the ingestion report. A stale second row likewise rolled back an earlier update. Existing caller transactions are refused without being committed or rolled back.
- Verified primary-key/check/foreign-key enforcement, `PRAGMA integrity_check=ok`, no foreign-key violations, repeatable initialization, and refusal of unsupported/unrelated databases.
- No external requests were needed. Tests use synthetic statistics and temporary databases; this phase does not independently validate live season coverage or resolve Houston's FGA discrepancy.

Files changed: `src/nba_mcp/database.py` implements storage; `tests/test_database.py` adds database validation; `tests/test_project_setup.py` now keeps only scoring/MCP modules behind their future-phase boundary; `AGENTS.md` records Phase 3 authorization; README, architecture, dictionary, and work plan document the schema, functions, usage, and limits. No runtime dependencies or project directories were added.

Limitations at Phase 3 completion: the schema targets the approved season only; no automatic migrations, deletion reconciliation, historical statistics snapshots, server, or scoring was implemented in that phase. Per-row provenance retains original batch reports, not a recomputed database-wide completeness status. Python 3.11 was not separately runtime-tested. No live-season database, commit, or push was created. Phase 4 was awaiting approval at that checkpoint and was subsequently authorized.

## Phase 4 validation record

Validated October 14, 2026 with Python 3.12:

- Verified the formula against Basketball Reference and Sports Reference publisher glossaries (linked in the dictionary).
- `python -m unittest discover -s tests -v`: **46 tests passed**, including 11 new scoring tests and all 35 earlier tests.
- Independently worked synthetic cases yield 24.8 (balanced game), 7.2 (free throws only), 8.0 (three-point scoring), -8.9 (negative game), and 0.0 (zero statistics).
- Verified all required fields reject missing/invalid inputs, made shots cannot exceed attempts, plus-minus is separate and score-invariant, provenance is returned, and inputs remain unchanged.
- No NBA requests, live data imports, SQLite schema changes, additional dependencies, or MCP tools were needed. No commit or push was made.

Files: `scoring.py` implements pure scoring and evaluation; `tests/test_scoring.py` provides independent examples and validation tests; setup tests now keep only MCP behind its approval gate; AGENTS records authorization; README, architecture, dictionary, and this plan document functions, policies, and results.

Limitations at Phase 4 completion: the Houston FGA source discrepancy remained unresolved and scoring did not certify input quality. Python 3.11 was not separately tested. No rankings, aggregations, labels, or agent integration were added in that phase. Phase 5 was awaiting approval and was subsequently authorized.

## Phase 5 validation record

Validated October 14, 2026 with Python 3.12, FastMCP 4.1.0, and MCP 2.3.0:

- `python -m unittest discover -s tests -v`: **58 tests passed**, including 12 new server/contract tests and all 46 prior tests.
- In-process MCP clients verified discovery, input/output schemas, read-only annotations, strict types, unknown/missing arguments, date/pagination behavior, source/coverage preservation, separate plus-minus, formula provenance, not-found semantics, and application error codes.
- A real subprocess stdio client started `python -m nba_mcp.server --database <temporary fixture file>`, listed all three tools, and evaluated a fixture to 11.4 with source coverage retained.
- SQLite rejected write attempts on the server connection. Read-only access handled a path containing spaces and `#`, did not create a missing file, and left the fixture database's bytes unchanged after queries/evaluation.
- Editable installation succeeded with FastMCP dependency metadata and console entry point. `nba-mcp-server --help` succeeded; `pip check` found no broken requirements.
- No NBA requests, live-season database population, actual agent acceptance, HTTP listener, user MCP-client configuration, commits, or pushes were added. This is the Phase 5 contract/smoke scope, not Phase 6 acceptance.

Files changed: `server.py` implements adapters/startup/error translation; `database.py` adds a separate read-only opener without changing schema version 1; `pyproject.toml` pins FastMCP and registers the command; `tests/test_server.py` adds contract and stdio tests; setup tests track the new dependency/entry point and keep package initialization inert; AGENTS records authorization; README, architecture, dictionary, and this plan explain functions, contracts, startup, and limits.

Limitations at Phase 5 completion: no live season file was provisioned in that phase; IDs must be known (no name lookup tool); no remote transport, rankings, refresh/import MCP tool, or deployment was included. Transitive dependencies were not locked. Python 3.11 and a separate desktop MCP client were not runtime-tested. Phase 6 was awaiting approval and was subsequently authorized.

## Phase 6 validation record — October 16, 2026

- Offline suite: `python -m unittest discover -s tests -v` — **65 tests passed** (58 prior tests, 3 new full-workflow tests, 4 live-verifier helper tests). No live NBA requests are made by discovery.
- Live command: `python tests/live_validate.py --database nba.sqlite3 --report docs/phase6_live_report.json --timeout 15`. Initial sandbox DNS access failed before ingestion; the authorized network-enabled run succeeded. UTC evidence timestamp: `2026-10-16T05:58:13.581160+00:00` (October 16, 12:58 a.m. America/Chicago).
- Retrieved and persisted **26,651 appearances**, **582 players**, **1,230 games**, **2,460 team-game references**, spanning **2025-10-21 through 2026-04-12**.
- Player-season summary endpoint (`LeagueDashPlayerStats`, Base/Totals/Regular Season) agreed on every player ID, GP, and additive statistic. No missing/unexpected players or per-player discrepancies were found.
- Team reference retained the known issue: Houston, `0022500792`, player FGA **86**, team-log FGA **85**. `coverage.checks_passed=False` and `verified_complete=False` remain unchanged. No correction was inferred.
- All **26,651** calculated Game Scores matched the separate Decimal reference implementation. Re-import of the same batch reused its ingestion ID. SQLite integrity was `ok`, with no foreign-key violations.
- Actual stdio subprocess tools returned six source-backed evaluations: first record, highest/lowest Game Score in the retrieved dataset, a zero-minute appearance, a player with multi-team history, and a Houston appearance from the disputed game. A 56-appearance history, pagination, NOT_FOUND, and invalid-argument scenarios passed. All results retained source/coverage/formula context. Database bytes were unchanged by MCP calls.
- `pip check`: no broken requirements. Git ignore verification confirmed database and SQLite sidecars are excluded. No runtime dependency, production tool, formula, or schema change was introduced.

### Agent-use scenarios and interpretation

These are scripted MCP workflows plus review of their returned evidence, not a separate autonomous LLM benchmark or desktop-client test.

| User-style request / scenario | Evidence / expected interpretation |
| --- | --- |
| Show a player's game history, then evaluate a selected game | Synthetic fixture retrieval -> normalization -> import -> real stdio history -> 11.4 Game Score; raw data and failing coverage remain visible |
| Follow a player across teams and paginate to a brief appearance | Fixture keeps DAL/BOS team context and returns a 0.0 score for a legitimate brief zero-stat game |
| Refresh corrected source data | Fixture assist correction changes 11.4 to 12.1 after reopening storage; plus-minus change to +50 does not affect the score |
| Refresh fails or contains missing statistics | Existing successful import remains available; no fabricated zero-stat replacement or partial import |
| Evaluate Alex Caruso on October 21, 2025 | Live tool result: Game Score **7.5**, plus-minus **-15**, source/formula provenance and original coverage retained |
| Evaluate the highest/lowest scores in this retrieved dataset | Bam Adebayo, March 10, 2026: **60.5**, plus-minus **+20**; Keyonte George, November 30, 2025: **-10.2**, plus-minus **-27**. These are calculated dataset extrema, not universal performance labels |
| Inspect the Houston game with source disagreement | Dorian Finney-Smith, February 19, 2026: **5.6**, plus-minus **+6**; team-log FGA issue remains visible and unresolved |
| Request an unknown appearance / invalid page | Explicit NOT_FOUND / INVALID_ARGUMENT errors; no guessed player statistics |

### Files and release recommendation

`tests/test_integration.py` adds full-path fixtures and correction/failure scenarios; `tests/live_validate.py` provides the opt-in reproducible live run; `tests/test_live_validation.py` checks summary/reference helpers; `docs/phase6_live_report.json` records timestamped live evidence; `.gitignore` excludes database sidecars; AGENTS records Phase 6 authorization and response tracking; README, architecture, dictionary, and this plan document operational behavior, functions, results, and limits. `nba.sqlite3` is a generated ignored local dataset, not a source artifact.

Recommend **local owner evaluation with explicit source-quality qualification**. Do not label the dataset independently certified or claim production/desktop-client acceptance. Known limits: shared-provider references, unresolved Houston FGA discrepancy, ID-only lookup, no deletion reconciliation or historical row snapshots, no remote transport, no external desktop/LLM harness, Python 3.11 not separately tested, and unlocked transitive dependencies. Further client configuration, source remediation, release, deployment, or feature expansion requires owner direction/approval. No commit, push, or public release has been performed. Phase 6 is complete within this local validation scope; there is no approved next phase.
