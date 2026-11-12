# apply-spy

A local-first tool for organizing job applications and finding jobs that fit your resume and preferences. Built for a household with more than one job seeker (for example, a new grad and an experienced engineer), each with their own profile.

> **Status:** planning / early development. Nothing below is implemented yet; this document describes the target design.

## Why

- Job searches turn into a mess of spreadsheets, tabs and emails.
- Job boards are noisy, and a new-grad search looks nothing like a senior one.
- Resumes and application history are sensitive, so everything stays on your machine.

## Goals

1. **Track** every application, contact, deadline and interview in one place.
2. **Discover** relevant jobs from public sources.
3. **Rank** jobs per person, with a clear explanation of why each one matches.
4. **Stay local:** no accounts, no cloud storage of personal data.

## Features

### Core (MVP)

- Multiple profiles (one per person) in a shared local database
- Application tracker with stages: Saved → Applied → Screen → OA → Interviews → Offer / Rejected / Ghosted
- Per-application notes, links, contacts, resume version used, and salary info
- Follow-up and deadline reminders
- Search, filter and list views

### Resume and profile

- Resume import (PDF / DOCX) parsed into structured data: skills, experience, education, projects
- Editable profile with preferences: locations or remote, salary range, work authorization, role types, deal-breakers
- Multiple resume versions per person

### Job discovery

- Ingest listings from public sources (Greenhouse, Lever and Ashby job boards, official job APIs, curated lists)
- Deduplication and normalization
- New-match alerts

### Matching

- Rule-based filters (location, seniority, required skills, work authorization)
- Semantic similarity between resume and job description
- Score with explanation, such as "matches 7 of 9 required skills; missing Kubernetes"

### Planned extras

- Resume-to-job gap analysis and tailoring suggestions
- Gmail sync to detect confirmations, rejections and interview invites
- Browser extension to save a job from any page
- Interview prep notes and question bank
- Networking and referral tracker
- Insights: response rate, funnel conversion, which resume version performs best
- Household views: shared companies, combined city fit
- Offer comparison and calendar (iCal) export

## Tech stack (proposed)

| Area           | Choice                                                              |
| -------------- | ------------------------------------------------------------------- |
| Language       | Python 3.12+                                                        |
| UI             | Streamlit or NiceGUI (local web UI), with a CLI for quick actions   |
| Storage        | SQLite, with SQLAlchemy                                             |
| Resume parsing | `pdfplumber` / `python-docx`, with optional LLM-assisted extraction |
| Matching       | Rules plus local embeddings (`sentence-transformers`)               |
| Job fetching   | `httpx` with scheduled runs (cron or APScheduler)                   |
| LLM (optional) | Claude API for tailoring and gap analysis; off by default           |
| Tooling        | `uv`, `ruff`, `pytest`                                              |

## Project layout (planned)

```
apply-spy/
├── src/apply_spy/
│   ├── db/          # models, migrations
│   ├── profiles/    # resume parsing, preferences
│   ├── tracker/     # applications, stages, reminders
│   ├── sources/     # job source fetchers
│   ├── matching/    # filters, embeddings, scoring
│   ├── ui/          # local web UI
│   └── cli.py
├── tests/
├── data/            # local DB and resumes (gitignored)
└── readme.md
```

## Roadmap

- [ ] **v0.1:** SQLite schema, profiles, manual application tracker (CLI and basic UI)
- [ ] **v0.2:** Resume import and structured profile with preferences
- [ ] **v0.3:** Job ingestion from 2–3 sources, with dedupe and rule-based filtering
- [ ] **v0.4:** Embedding-based matching with score explanations
- [ ] **v0.5:** Reminders, dashboard and insights
- [ ] **v0.6:** Gmail sync and browser extension
- [ ] **Later:** tailoring, interview prep, networking, offer comparison

## Privacy and data

- All data lives in `data/` on your machine and is gitignored.
- No telemetry. Network access is only used to fetch public job listings.
- Any LLM features are opt-in and send only the text you choose, using your own API key.

## Sourcing jobs responsibly

apply-spy prefers official APIs and public ATS endpoints. It does not scrape sites whose terms forbid it (such as LinkedIn or Indeed). Respect each source's rate limits and terms of service.
