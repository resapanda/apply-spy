# AGENTS.md

Guidance for AI coding agents (and humans) working on apply-spy.

## What this project is

A local-first Python tool for two job seekers in one household: a new grad (graduating 2027, internship or full-time) and an experienced SWE (5 years). It tracks applications and finds and ranks jobs that fit each person's resume and preferences. Target roles are software engineering in the Seattle and Mountain View areas.

Read these first:
- [readme.md](readme.md): overview, proposed stack, roadmap
- [docs/requirements.md](docs/requirements.md): numbered requirements (P1, T1, M2...) and decisions
- [docs/sprint-plan.md](docs/sprint-plan.md): user stories with acceptance criteria, by sprint

Work items live in GitHub Issues (one per user story, grouped by sprint milestone). The issue and its acceptance criteria are the definition of done.

## Current status

Sprint 1 (v0.1) in progress. Done: profiles (1.1-1.3), the application tracker (2.1-2.3: quick-add, status changes with history, editing, filters, search, sort, table and board views), CSV export (3.1), backup and restore (3.2) and follow-up dates (4.1). Still to do in Sprint 1: the "Needs attention" page (4.2). Databases made by older versions get new nullable columns added automatically by `init_db` (see `db/session.py`); anything bigger needs a real migration. Update this section as the project moves.

## Decisions already made (do not re-ask)

- Language: Python 3.12+, managed with `uv`.
- Storage: SQLite via SQLAlchemy, in a local `data/` folder (gitignored).
- UI: local web UI with NiceGUI, with a CLI as a secondary interface. The server must bind to 127.0.0.1 only.
- Everything works offline except job fetching. No telemetry.
- LLM features are optional and off by default; the core must work without them. Never send resume text anywhere without an explicit opt-in.
- No visa sponsorship filtering. Locations: Seattle area and Mountain View area.
- Profiles are visible to each other by default.
- One job feed for internships and full-time roles, with sorting and filtering by job type.

## Architecture rules

- **Service layer first.** All logic lives in `src/apply_spy/` modules (tracker, profiles, sources, matching). The web UI and CLI only call these functions; no business logic in UI code.
- **Profile isolation.** Every query that touches personal data takes a `profile_id` and filters by it. Add a test when adding a new table.
- **Status changes** go through one function that updates the status and appends a history event in a single transaction.
- **Job sources** are one adapter per source, all returning the same normalized job record. A failing source must never stop the others. Fetching must be idempotent.
- **Scoring** must be explainable: store each component and a plain-language reason with the score. Scoring runs offline.
- Planned layout is in the readme. Keep to it unless there is a reason, and update the readme if it changes.

## Working agreements

- Work one issue at a time, on its own branch (for example `1-1-create-profile`). Never commit to `main` directly.
- Check the issue's acceptance criteria before and after the change, and tell the user which ones are met.
- Write tests for new logic (`pytest`). Parsers use saved sample files; source adapters use saved sample responses, never live network calls in tests.
- Keep changes small and focused. Do not refactor unrelated code or add features that are not in an issue; suggest them instead.
- Ask before adding a dependency, and say why it is needed.
- Ask before any outward-facing or hard-to-undo action: pushing, opening or merging PRs, creating or closing issues, deleting data.
- Do not use real resumes or personal data in tests, fixtures or commits. Use made-up sample data.

## Commands

```bash
uv sync                        # install dependencies
uv run pytest                  # run tests
uv run ruff check .            # lint
uv run ruff format .           # format
uv run apply-spy serve         # web UI at http://localhost:8080
uv run apply-spy profile add NAME   # CLI: create a profile
uv run apply-spy profile list       # CLI: list profiles
uv run apply-spy export ID -o FILE  # CLI: export a profile's applications to CSV
uv run apply-spy backup             # save a timestamped zip of all data (data/backups)
uv run apply-spy restore FILE.zip   # restore a backup (asks before replacing data)
uv run apply-spy check              # database self-check: row counts and integrity
```

Set `APPLY_SPY_DATA_DIR` to use a different data folder (handy for experiments and tests).

## Git and PR conventions

- Branch names: `<issue-number>-short-description`.
- Commit messages: short imperative summary ("Add profile creation").
- PR description: what changed, and `Closes #<issue number>` so the issue closes on merge.
- Keep PRs to one issue where possible.

## Privacy and compliance

- `data/` and any resume files are never committed. Check `.gitignore` before the first commit that creates data.
- Fetch jobs only from official APIs and public job-board endpoints. Do not scrape sites whose terms forbid it (for example LinkedIn, Indeed). Respect rate limits and use a descriptive user agent.

## About the user

Learning-friendly collaboration: explain what you are doing and why in plain language, especially for git, testing and architecture choices. Prefer clear, simple solutions over clever ones.
