# apply-spy: Requirements

Status: draft for review. Nothing here is built yet. Items marked **[?]** are open questions to settle before coding (see the last section).

## 1. Purpose

apply-spy gives a household of job seekers one local place to (a) keep track of every application and (b) see which open jobs fit each person, with reasons.

## 2. Users

| | Persona A: new grad | Persona B: experienced SWE |
| --- | --- | --- |
| Situation | Graduating 2027; wants internship or full-time | 5 years of SWE experience; wants full-time |
| Needs | Many applications at once; deadlines and OAs; roles that accept new grads | Fewer, higher-quality applications; seniority and compensation fit; referrals |
| Pain | Volume and tracking | Noise from junior and mismatched roles |

Both share one installation and one database but see their own profile, pipeline and matches by default. A "household" view can combine them.

## 3. What the user gets (deliverables by release)

Each release is usable on its own.

### v0.1: "I can track my applications"
You can:
- Create a profile for yourself and your husband.
- Add an application (company, role, link, date, status, notes) in under 30 seconds.
- Move it through stages and see everything on a board and in a table.
- Filter and search by status, company or date.
- Get a list of "needs attention" items (follow-ups due, deadlines coming up).

You get: a working local app, a database file you own, and a CSV export.

### v0.2: "The app knows my background"
You can:
- Import a resume (PDF or DOCX) and review the extracted skills, experience and education.
- Fix anything wrong, and save the profile.
- Set preferences: locations or remote, role types, seniority, salary floor, work authorization, deal-breakers.
- Keep several resume versions and record which one you used for each application.

### v0.3: "The app finds jobs"
You can:
- Add job sources (company career pages on Greenhouse, Lever or Ashby, plus one or two APIs or curated lists).
- See a deduplicated feed of new jobs, filtered by your hard preferences.
- Save a job to the tracker in one click.
- Hide jobs you don't want, so they never come back.

### v0.4: "The app ranks jobs for me"
You can:
- See each job scored for you, sorted best-first.
- Read why: matched skills, missing skills, seniority fit, location fit.
- Adjust weights (for example, "location matters more than company size").
- Mark a match as good or bad, which improves later ranking.

### v0.5: "The app keeps me on track"
You can:
- Get reminders for follow-ups and deadlines (desktop notification and a daily summary).
- See a dashboard: applications per week, response rate, funnel by stage, stale applications.
- See which resume version gets more responses.

### v0.6 and later
Gmail sync, browser extension, resume tailoring and gap analysis, interview prep notes, networking tracker, offer comparison, household views. Each will get its own requirements section before it is built.

## 4. Functional requirements

Priority: **M** = must (in its release), **S** = should, **C** = could.

### 4.1 Profiles

| ID | Requirement | Pri |
| --- | --- | --- |
| P1 | Create, rename and delete profiles. Each profile owns its applications, resumes, preferences and matches. | M |
| P2 | Switch the active profile from any screen. | M |
| P3 | Deleting a profile requires confirmation and removes its data. | M |
| P4 | Household view that combines profiles (for example, applications at the same company). | C |

### 4.2 Application tracker

| ID | Requirement | Pri |
| --- | --- | --- |
| T1 | Fields: company, role title, job URL, location, source, date applied, status, resume version, salary range, notes. | M |
| T2 | Statuses: Saved, Applied, Recruiter screen, Online assessment, Interview, Offer, Accepted, Rejected, Withdrawn, Ghosted. Users can add custom statuses. | M |
| T3 | Every status change is logged with a timestamp (history, not just current state). | M |
| T4 | Board view (columns by status) and table view (sortable, filterable). | M |
| T5 | Contacts per application (name, role, email, notes). | S |
| T6 | Interview log per application (round, date, interviewers, notes, outcome). | S |
| T7 | Warn when adding a duplicate (same company and similar title, or same URL). | S |
| T8 | Mark an application "Ghosted" suggestion after N days without response (N configurable, default 21). | S |
| T9 | Export to CSV; import from CSV (so an existing spreadsheet can be loaded). | M (export), S (import) |
| T10 | Attach files (cover letter, offer letter) stored locally. | C |

**Acceptance example (T1, T3):** adding an application and changing it from Applied to Interview produces two history entries with times, visible in the application's detail view.

### 4.3 Reminders

| ID | Requirement | Pri |
| --- | --- | --- |
| R1 | Set a follow-up date or deadline on any application. | M |
| R2 | "Needs attention" list: overdue, due today, due this week. | M |
| R3 | Desktop notification and an optional daily digest (printed in the CLI or UI). | S |
| R4 | Export deadlines to an .ics calendar file. | C |

### 4.4 Resume and profile intelligence

| ID | Requirement | Pri |
| --- | --- | --- |
| S1 | Import PDF and DOCX resumes. | M |
| S2 | Extract name, contact info, education, experience (company, title, dates, bullets), skills and projects. | M |
| S3 | Show extracted data side by side with the original for review; every field is editable. The app never silently trusts parsing. | M |
| S4 | Store multiple resume versions per profile with a label and date. | M |
| S5 | Preferences: target roles, seniority, locations, remote policy, salary floor, work authorization, industries to include or avoid, companies to exclude, deal-breaker keywords. | M |
| S6 | Skill normalization (for example, "JS", "Javascript" and "JavaScript" are one skill). | S |
| S7 | Parsing can optionally use an LLM for better accuracy. Off by default, and the user sees exactly what text is sent. | C |

### 4.5 Job sources and ingestion

| ID | Requirement | Pri |
| --- | --- | --- |
| J1 | Source adapters for Greenhouse, Lever and Ashby public job boards, configured by a list of company board URLs or slugs. | M |
| J2 | Each job is normalized to: title, company, location, remote flag, seniority, description, requirements, URL, posted date, source. | M |
| J3 | Deduplicate across sources and across runs. | M |
| J4 | Fetch manually (a button or command) and on a schedule. | M (manual), S (scheduled) |
| J5 | Respect rate limits, `robots.txt` and each source's terms. No scraping of sites that forbid it. | M |
| J6 | Keep a record of when each job was first seen and when it disappeared (so closed postings are marked). | S |
| J7 | Import a job manually by pasting a URL or description. | M |
| J8 | Curated lists (for example, a public internship repository) as an additional source. | S |
| J9 | Optional job API sources that offer a free tier. | C |

### 4.6 Matching and ranking

| ID | Requirement | Pri |
| --- | --- | --- |
| M1 | **Hard filters** from preferences (location, work authorization, excluded companies, deal-breaker keywords) remove jobs before scoring. | M |
| M2 | **Seniority fit:** detect level from title and description (intern, new grad, junior, mid, senior, staff). Persona A sees new-grad and intern roles; Persona B sees mid-level and senior roles. | M |
| M3 | **Skill match:** compare required and preferred skills in the job against the profile's skills. | M |
| M4 | **Semantic similarity** between resume and job description using local embeddings. | S |
| M5 | Final score from 0 to 100, with a breakdown of the parts (skills, seniority, location, similarity, preferences). | M |
| M6 | Every score has a plain-language explanation, including what is missing. | M |
| M7 | User-adjustable weights; user feedback (good or bad match) is stored and can tune ranking. | S |
| M8 | Scoring runs entirely offline after the job data is fetched. | M |

**Acceptance example (M2, M6):** for a posting titled "Senior Backend Engineer, 5+ years", Persona A's match list excludes it or ranks it low with the reason "seniority mismatch", while Persona B ranks it by skills and location.

### 4.7 Dashboard and insights

| ID | Requirement | Pri |
| --- | --- | --- |
| D1 | Counts by status; applications per week. | M (v0.5) |
| D2 | Response rate and time-to-first-response, per resume version and per source. | S |
| D3 | Stale applications list. | S |
| D4 | Weekly summary of new matches and pending actions. | C |

### 4.8 Interfaces

| ID | Requirement | Pri |
| --- | --- | --- |
| U1 | CLI covering all core actions (add, list, update, import, fetch, match, export). | M |
| U2 | Local web UI at `localhost` for the board, forms, profile review and match feed. | M |
| U3 | Both interfaces use the same underlying code, with no logic duplicated in the UI. | M |
| U4 | Works without internet, except for fetching jobs and optional LLM calls. | M |

## 5. Data the system stores

Main entities and how they relate:

- **Profile**: one per person.
- **Resume**: belongs to a profile; has a label, file, and parsed data.
- **Preference set**: one per profile.
- **Job**: from a source; unique after dedupe. Shared by all profiles.
- **Match**: the pairing of a profile and a job, with score, breakdown, explanation and the user's feedback.
- **Application**: a profile applying to a job (or to a manually entered one), with status history.
- **Status event**: application, old status, new status, time.
- **Contact**, **Interview**, **Reminder**: belong to an application.
- **Source**: configured job source and its last fetch time.

Resume files and the database live in a local `data/` folder.

## 6. Non-functional requirements

| Area | Requirement |
| --- | --- |
| Privacy | All personal data stays on disk locally. No telemetry. No network calls except job fetching and explicitly enabled LLM features. |
| Safety of data | One-command backup and restore of the whole `data/` folder. Schema migrations never lose data. |
| Performance | Board loads in under 1 second with 1,000 applications. Scoring 5,000 jobs completes in under 2 minutes on a laptop. |
| Reliability | A failing source never blocks others; errors are shown with the source name. Re-running a fetch is safe (idempotent). |
| Transparency | Parsing and scoring results are always explainable and editable. |
| Setup | Install and first run in under 10 minutes using `uv`. Works on macOS first; Linux and Windows are best-effort. |
| Testing | Core logic (tracker, filters, scoring, dedupe) has automated tests; parsers are tested against sample resumes. |

## 7. Out of scope (for now)

- Auto-submitting applications.
- Scraping LinkedIn, Indeed or other sites that prohibit it.
- Hosting, multi-household accounts, or syncing across devices.
- A mobile app.
- Guaranteeing job quality or hiring outcomes.

## 8. Risks and how we handle them

| Risk | Handling |
| --- | --- |
| Resume parsing is unreliable | Mandatory review screen (S3); optional LLM assist later |
| Few jobs available from allowed sources | Manual import (J7), curated lists (J8), more ATS adapters |
| Seniority detection is noisy | Rule-based first, show the reason, allow user override |
| "Any company" is too broad for Greenhouse/Lever/Ashby, and big employers in Seattle and Mountain View (Amazon, Microsoft, Google and others) use their own career sites | Start with a seed list of companies that use those three systems; add manual import (J7) and look at per-company adapters or official APIs for the biggest employers later |
| Source formats change | One adapter per source with tests on saved sample responses |
| Scope creep | Ship in the release order above; each release must be usable alone |

## 9. Success criteria

After using it for a month, you should be able to say:
- Every application I made is recorded, and I never missed a follow-up because I forgot.
- The top 20 matches in my feed are mostly roles I'd actually apply to.
- Adding a job takes under 30 seconds.
- I can see which kinds of applications get responses.

## 10. Decisions

Settled so far:

| # | Question | Decision |
| --- | --- | --- |
| 1 | Target roles and companies | Software engineer roles at any company. Discovery must therefore be broad, not a short company list. |
| 2 | Visa sponsorship | Not needed. Sponsorship filtering is dropped; work authorization is a simple "authorized" setting. |
| 3 | Locations | Seattle area and Mountain View (Bay Area peninsula). Remote roles are an optional extra. |
| 4 | Internship vs full-time | One feed, with a type field and sorting/filtering by it (see M9). |
| 5 | UI | Local web UI, with the CLI as a secondary interface. Framework (Streamlit or NiceGUI) to be picked at v0.1. |
| 6 | LLM | Not decided; core features work without one (see below). |
| 7 | Privacy between profiles | Either is fine, so profiles are visible to each other by default. P4 (household view) moves up to "should". |
| 8 | Spreadsheet import | Not needed. T9 import drops to "could". |

Still open:
- **LLM use.** An LLM (large language model) is an AI like Claude or ChatGPT. Here it would only be an optional helper for better resume parsing, resume tailoring and gap analysis. It would send the text you choose to an outside service. Everything in v0.1 to v0.5 works without it, so this can wait.
- **Remote roles:** include them for both of you, or only Seattle and Mountain View?
- **Seed list of companies** to pull from (see risks). We can start with a list we build together.

## 11. Changes from these decisions

- **M9 (must):** the job feed can be sorted by score, date posted, company and job type (internship or full-time), and filtered by job type and location.
- **M1:** location filter is preset to Seattle area and Mountain View area, with a configurable radius.
- **Sponsorship filtering** removed from S5 and M1.
- **P4** raised to should; **T9 import** lowered to could.
