# Project development agreement

## Scope and approval

- Develop only the currently approved phase. The owner has authorized Phases 1 through 6. Release, deployment, and significant additional scope require separate explicit approval.
- Never start the next phase without explicit owner approval, even after all checks pass.
- Obtain owner approval before significant changes to architecture, dependencies, dataset scope, scoring behavior, or the approved project structure.
- Routine edits and validation within an approved phase may proceed without repeated approval.
- Keep the implementation simple. Avoid speculative frameworks, services, abstractions, or configuration systems.
- Phase 6 permits full-workflow tests, live retrieval/storage/coverage verification, agent-use scenarios, operational documentation, and a release recommendation. Plus-minus remains separate context. Do not deploy, publish, or expand tool scope without approval.
- Begin each user-facing response with a tracker listing the six phases and the active phase's steps, updating statuses as evidence becomes available.

## Development and documentation

- Use Python 3.11+ and the `src/nba_mcp` layout.
- Keep MCP adapters separate from retrieval, persistence, and scoring business logic.
- Explain every new file and major function in documentation and completion reports. Give implemented public functions clear docstrings describing inputs, outputs, errors, and side effects.
- Update README, architecture, data dictionary, and work plan alongside relevant code changes.
- Document assumptions, missing-data behavior, source provenance, and meaningful trade-offs in plain English.
- Add dependencies only when needed by the approved phase; SQLite and unittest use the standard library.
- Do not commit credentials, local environments, caches, or generated databases.

## Testing

- Run appropriate tests before declaring a phase complete and record actual results.
- Default command: `python -m unittest discover -s tests -v`.
- Keep routine tests deterministic and offline. Use fixtures/mocks for upstream behavior; separately label authorized live checks.
- Validate data keys, types, missing values, traded-player cases, and season boundaries when those features are introduced.
- Test scoring against independently calculated examples, persistence against temporary databases, and MCP tools against their public contracts in the relevant phases.
- Report blocked or unperformed checks honestly; do not claim live data completeness from mock tests.

## Phase completion report

Include: (1) phase status, (2) files created or modified, (3) explanation of each file and major function, (4) architectural decisions and reasoning, (5) tests and results, (6) issues or limitations, (7) recommended next phase with benefits and trade-offs, and (8) owner approval status.

Stop after the report. Record the next phase as awaiting owner approval until approval is explicitly received.
