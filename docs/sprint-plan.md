# apply-spy: Sprint plan and user stories

Companion to [requirements.md](requirements.md). Each feature has an approach ("how we achieve it") and 2–3 user stories written as index cards: the front is the story, the back is the acceptance criteria (functional, then quality attributes).

**Conventions used in the criteria**
- "The app" is the local web UI backed by the Python service layer and a SQLite file in `data/`. The CLI calls the same service functions.
- "Given the app is running" means the service layer is available and the database is migrated.
- Measurable limits assume a normal laptop.

## Sprint goals

| Sprint | Release | Goal |
| --- | --- | --- |
| 1 | v0.1 | Both of us can track applications locally and never lose the data. |
| 2 | v0.2 | The app understands each person's resume and preferences. |
| 3 | v0.3 | The app brings in jobs from public sources without duplicates. |
| 4 | v0.4 | Jobs are ranked per person with clear explanations and sorting. |
| 5 | v0.5 | The app reminds us and shows what is working. |

---

# Sprint 1 (v0.1): Track applications

## Feature 1: Profiles

**How we achieve it:** a `profiles` table; every other table has a `profile_id`. The web UI keeps the active profile in session state and the service layer takes `profile_id` as an argument, so no query can return another profile's rows by accident. Profiles are visible to each other by default (decision 7).

### Story 1.1: Create a profile
*(Front)*
"As a job seeker, I want to create a profile with my name so that my applications, resumes and matches are kept separate from my husband's."

*(Back)* Acceptance criteria

Functional
- Given the app is running and no profile named "Haruka" exists
- When I submit the create-profile form with the name "Haruka"
- Then a profile is stored with that name and a creation time
- And I see it in the profile list.

Quality attributes
- Usability: creating a profile takes one field and one click.
- Data integrity: submitting an empty name or a duplicate name shows an error and creates nothing.

### Story 1.2: Switch the active profile
*(Front)*
"As one of two users on the same computer, I want to switch between profiles so that I only see my own pipeline until I choose to look at his."

*(Back)* Acceptance criteria

Functional
- Given two profiles exist, each with different applications
- When I select the second profile in the profile switcher
- Then the board, table and feed show only that profile's data
- And the switcher shows the active profile name on every page.

Quality attributes
- Correctness: across 100 switches between two profiles, no application from the other profile ever appears (verified by an automated test).
- Performance: the switch completes in under 500 ms.

### Story 1.3: Delete a profile safely
*(Front)*
"As a user, I want to delete a profile, and be asked to confirm first, so that I never lose a person's data by a misclick."

*(Back)* Acceptance criteria

Functional
- Given a profile with applications exists
- When I click delete and confirm by typing the profile name
- Then the profile and all its owned data (applications, resumes, matches) are removed
- And cancelling at the prompt changes nothing.

Quality attributes
- Safety: deletion cannot be completed without the typed confirmation.
- Data integrity: no orphaned rows remain (verified by a foreign-key check after deletion).

---

## Feature 2: Application tracker

**How we achieve it:** tables for `applications` and `status_events`. A service function `change_status(application_id, new_status)` updates the current status and appends a status event in one database transaction. The board and table are two views over the same query, built with the web UI's components. Quick-add uses a short form with only company and title required.

### Story 2.1: Add an application quickly
*(Front)*
"As a job seeker applying to many roles, I want to add an application with just a company, a title and a link so that tracking never slows down my applying."

*(Back)* Acceptance criteria

Functional
- Given I am on my profile
- When I enter company "Acme", title "Software Engineer" and a URL, and submit
- Then an application is created with status "Applied" and today's date
- And it appears at the top of the table and in the Applied column.
- And optional fields (location, salary range, resume version, notes) can be added now or later.

Quality attributes
- Usability: adding an application takes under 30 seconds with only the required fields.
- Data integrity: a missing company or title is rejected with a message naming the field.

### Story 2.2: Move an application through the pipeline
*(Front)*
"As a job seeker, I want to change an application's status as things happen so that I can see where each application stands and when it changed."

*(Back)* Acceptance criteria

Functional
- Given an application with status "Applied"
- When I change its status to "Online assessment" and then to "Interview"
- Then its current status is "Interview"
- And its detail page lists two status events, each with old status, new status and time, in order.

Quality attributes
- Reliability: if saving fails midway, neither the status nor the history changes (both are written in one transaction).
- Auditability: status events cannot be edited through the UI.

### Story 2.3: Find applications with board, table, filters and search
*(Front)*
"As a user with many applications, I want a board view, a sortable table, and filters so that I can quickly find what needs my attention."

*(Back)* Acceptance criteria

Functional
- Given 50 applications across several statuses and companies
- When I filter by status "Interview" and search for "acme"
- Then only matching applications are shown, in both table and board views
- And I can sort the table by date applied, company or status.

Quality attributes
- Performance: the board and table load in under 1 second with 1,000 applications.
- Usability: the selected filters stay applied when I open an application and come back.

---

## Feature 3: Data ownership

**How we achieve it:** one SQLite file plus a `data/resumes/` folder, so a backup is a copy of `data/`. A `backup` command zips the folder with a timestamp. CSV export is a query written through Python's `csv` module.

### Story 3.1: Export my applications to CSV
*(Front)*
"As a user, I want to export my applications to a CSV file so that I can open them in a spreadsheet or keep my own copy."

*(Back)* Acceptance criteria

Functional
- Given a profile with applications
- When I choose Export CSV
- Then I receive a file with one row per application and a header row naming every field
- And the file opens in a spreadsheet with correct columns, including notes that contain commas or line breaks.

Quality attributes
- Interoperability: the file is UTF-8 and parses correctly in a standard CSV reader (verified by a round-trip test).
- Performance: exporting 1,000 applications takes under 3 seconds.

### Story 3.2: Back up and restore all data
*(Front)*
"As a user storing my only copy of my job search locally, I want a one-step backup and restore so that a lost laptop or bad update never costs me my history."

*(Back)* Acceptance criteria

Functional
- Given the app has data
- When I run the backup command
- Then a timestamped archive containing the database and resume files is created
- And restoring that archive into an empty `data/` folder brings back every profile and application.

Quality attributes
- Reliability: a restore followed by the app's self-check reports the same row counts as before the backup.
- Safety: restore refuses to overwrite a non-empty `data/` folder without explicit confirmation.

---

## Feature 4: Follow-up reminders (basic)

**How we achieve it:** a nullable `follow_up_date` on each application. A "needs attention" query returns applications whose date is today or earlier, plus those due within seven days, grouped by urgency. Desktop notifications and scheduling are added in Sprint 5.

### Story 4.1: Set a follow-up or deadline
*(Front)*
"As a job seeker, I want to set a follow-up date on an application so that I remember to check in or finish an online assessment on time."

*(Back)* Acceptance criteria

Functional
- Given an application exists
- When I set a follow-up date and an optional label such as "OA due"
- Then the date and label are saved and shown on the application card and detail page.

Quality attributes
- Usability: a date can be set from the board card without opening the detail page.
- Data integrity: a date in an invalid format is rejected with a message.

### Story 4.2: See what needs attention today
*(Front)*
"As a busy user, I want one list of overdue and upcoming follow-ups so that I know what to do first each day."

*(Back)* Acceptance criteria

Functional
- Given applications with follow-up dates yesterday, today, in three days and in a month
- When I open the "Needs attention" page
- Then I see yesterday's under "Overdue", today's under "Today", the three-day one under "This week", and the month-away one is not listed.

Quality attributes
- Correctness: grouping uses the computer's local date, including around midnight (covered by a test using a fixed clock).
- Performance: the page loads in under 1 second with 1,000 applications.

---

# Sprint 2 (v0.2): Resume and preferences

## Feature 5: Resume import and preferences

**How we achieve it:** text extraction with `pdfplumber` (PDF) and `python-docx` (DOCX), then rule-based parsing into sections (education, experience, skills, projects) using heading detection and date patterns. Results are saved as structured JSON in a `parsed_data` column. A review screen shows the original text next to editable fields. A skills dictionary normalizes names ("JS" to "JavaScript"). Preferences are a structured record per profile. No network access is required.

### Story 5.1: Import a resume and review what was extracted
*(Front)*
"As a job seeker, I want to upload my resume and review the skills, experience and education the app found so that the app knows my background and I can fix any mistakes."

*(Back)* Acceptance criteria

Functional
- Given a profile and a text-based PDF resume with sections for education, experience and skills
- When I upload the file
- Then the app shows extracted education entries, experience entries (company, title, dates) and a skills list next to the original text
- And I can edit any field and save
- And nothing is used for matching until I save the reviewed version.

Quality attributes
- Accuracy: on three sample resumes (new grad, experienced, two-column layout), at least 90% of skills and all company names and dates are extracted correctly, or are visibly flagged as uncertain.
- Privacy: no network request is made during import (verified by running the test with the network disabled).

### Story 5.2: Keep several resume versions and record which one I used
*(Front)*
"As a user who tailors resumes, I want to store labelled versions and pick one for each application so that I can later learn which version works best."

*(Back)* Acceptance criteria

Functional
- Given a profile with two resume versions labelled "Backend" and "General"
- When I add an application and choose "Backend" as the resume version
- Then the application shows that version
- And deleting a version that is in use asks for confirmation and keeps the label on past applications.

Quality attributes
- Data integrity: the link between application and version survives export and backup/restore.
- Usability: choosing a version is a single dropdown on the application form.

### Story 5.3: Set my job preferences
*(Front)*
"As a job seeker, I want to say what I am looking for (roles, seniority, locations, salary, deal-breakers) so that I only see jobs worth my time."

*(Back)* Acceptance criteria

Functional
- Given a profile
- When I set target roles to "Software Engineer", seniority to "New grad", job types to "Internship and full-time", locations to "Seattle area, Mountain View area", a salary floor, and excluded companies
- Then the preferences are saved to my profile
- And reopening the page shows the same values.

Quality attributes
- Usability: all preferences fit on one page, with sensible defaults (Seattle and Mountain View preselected).
- Correctness: preferences saved for one profile never change the other's (covered by an automated test).

---

# Sprint 3 (v0.3): Find jobs

## Feature 6: Job sources and ingestion

**How we achieve it:** one adapter per source, all returning the same normalized job record. The Greenhouse, Lever and Ashby adapters read their public job-board JSON for each configured company. Jobs are stored with a stable key (source plus the source's job id) and a content hash, so a re-run updates rather than duplicates. Cross-source duplicates are detected by normalized company, title and location. Requests are made with `httpx`, one source at a time, with a delay and a timeout. A source failure is logged against that source and does not stop the others.

### Story 6.1: Add a source and fetch jobs
*(Front)*
"As a job seeker, I want to add companies to follow and fetch their open roles so that new jobs come to me instead of me visiting many career pages."

*(Back)* Acceptance criteria

Functional
- Given I add a company whose job board is on Greenhouse
- When I click Fetch
- Then its open jobs are stored with title, location, remote flag, description, URL and posted date
- And the Sources page shows the fetch time and the number of jobs found.

Quality attributes
- Reliability: when one of three sources returns an error, the other two still complete and the failed source is shown with its error message.
- Compliance: requests use the source's public endpoint, a delay between requests, and a descriptive user agent; no login or scraping of restricted sites.

### Story 6.2: Re-fetch without duplicates
*(Front)*
"As a user, I want to refresh jobs as often as I like without getting the same job twice so that my feed stays clean."

*(Back)* Acceptance criteria

Functional
- Given a source has already been fetched
- When I fetch it again and nothing has changed
- Then the job count is unchanged
- And when a job was removed by the company, it is marked "closed" and hidden from the default feed
- And when a job's description changed, the stored job is updated.

Quality attributes
- Idempotence: running the same fetch five times leaves the database identical (verified by a test with saved sample responses).
- Performance: fetching and storing 2,000 jobs takes under 2 minutes.

### Story 6.3: Add a job by pasting a link or description
*(Front)*
"As a user whose target company is not on a supported source, I want to add a job by hand so that I can still track it and see how it matches."

*(Back)* Acceptance criteria

Functional
- Given I am on the Add job page
- When I paste a job's URL, title, company and description text
- Then the job is stored with source "manual" and can be saved to my tracker
- And if a job with the same company and title already exists, I am warned before saving.

Quality attributes
- Usability: only company, title and URL are required.
- Privacy: the app does not fetch the pasted URL unless I ask it to.

---

# Sprint 4 (v0.4): Rank and browse jobs

## Feature 7: Matching and ranking

**How we achieve it:** a pipeline of small, testable steps.
1. Hard filters remove jobs (location, excluded companies, deal-breaker keywords).
2. A seniority detector reads the title and description (intern, new grad, junior, mid, senior, staff) with keyword rules and years-of-experience patterns, and compares it with the profile's target level.
3. A skills matcher extracts skills from the job (using the same skills dictionary as resumes) and compares required and preferred skills with the profile.
4. Local embeddings (`sentence-transformers`) measure how similar the resume is to the job text.
5. A weighted sum produces a 0–100 score, stored together with each component and a plain-language explanation.

Scores are computed per profile and job and stored, so the feed reads them rather than recomputing.

### Story 7.1: Show me only jobs at my level
*(Front)*
"As a new grad, I want senior roles filtered out, and as an experienced engineer, I want intern and junior roles filtered out, so that each of us sees roles we could actually get."

*(Back)* Acceptance criteria

Functional
- Given a job titled "Senior Backend Engineer, 5+ years" and a job titled "Software Engineer Intern"
- When I view the feed as a profile targeting "New grad / intern"
- Then the intern job is shown and the senior job is hidden or flagged "seniority mismatch"
- And when I view the feed as a profile targeting "Mid / senior", the result is reversed.

Quality attributes
- Accuracy: on a labelled set of 100 sample postings, seniority is detected correctly for at least 85%, and uncertain cases are labelled "unknown" rather than guessed.
- Transparency: every hidden job can be shown again with a "show filtered" toggle, with the reason displayed.

### Story 7.2: Understand why a job scored the way it did
*(Front)*
"As a job seeker, I want a score with reasons, including what I am missing, so that I can decide quickly whether to apply and what to emphasize."

*(Back)* Acceptance criteria

Functional
- Given a profile with skills Python and SQL, and a job requiring Python, SQL and Kubernetes
- When I open the job's match details
- Then I see a score from 0 to 100
- And the breakdown lists skills (2 of 3 required matched), seniority fit, location fit and resume similarity
- And a sentence states that Kubernetes is missing.

Quality attributes
- Explainability: the displayed components sum to the displayed score.
- Privacy and offline use: scoring runs with no network access once jobs are stored.
- Performance: scoring 5,000 jobs for one profile completes in under 2 minutes.

### Story 7.3: Teach the ranking what I like
*(Front)*
"As a user, I want to mark matches as good or bad and adjust what matters most so that the ranking gets closer to my taste."

*(Back)* Acceptance criteria

Functional
- Given a feed of scored jobs
- When I mark a job "not interested" with a reason and raise the weight on location
- Then the job disappears from my default feed
- And the next scoring run uses the new weight and shows the changed scores.

Quality attributes
- Reversibility: weights can be reset to defaults in one click.
- Isolation: feedback and weights apply only to the profile that gave them.

---

## Feature 8: Job feed

**How we achieve it:** a feed page that queries stored jobs joined with the active profile's matches. Sorting and filtering are done in the database query (`ORDER BY` and `WHERE`) rather than in the browser, so they stay fast at thousands of jobs. "Save to tracker" creates an application in one transaction and links it to the job.

### Story 8.1: Sort and filter one combined feed
*(Front)*
"As a user who wants to look at internships and full-time roles together, I want one feed I can sort by score, date or company and filter by job type and location so that I can explore without switching pages."

*(Back)* Acceptance criteria

Functional
- Given a feed containing internships and full-time jobs in Seattle and Mountain View
- When I filter by job type "Internship" and sort by "Date posted, newest first"
- Then only internships appear, newest first
- And removing the filter shows both types again
- And the sort options include score, date posted, company and job type.

Quality attributes
- Performance: re-sorting or re-filtering 5,000 jobs responds in under 1 second.
- Usability: the chosen sort and filters are remembered when I return.

### Story 8.2: Save a job to my tracker in one click
*(Front)*
"As a job seeker, I want to save an interesting job straight from the feed so that I do not have to retype it."

*(Back)* Acceptance criteria

Functional
- Given a job in the feed that is not yet tracked
- When I click Save
- Then an application is created with status "Saved", prefilled with company, title, location and URL, and linked to the job
- And the feed marks the job as saved
- And clicking Save again does not create a second application.

Quality attributes
- Data integrity: save is atomic; either the application and link exist or neither does.
- Usability: one click, with no required extra fields.

### Story 8.3: Hide jobs I never want to see again
*(Front)*
"As a user, I want to hide a job or a whole company so that it never returns on later fetches."

*(Back)* Acceptance criteria

Functional
- Given a job in the feed
- When I click Hide, or Hide company
- Then it disappears from my feed
- And after the next fetch it still does not appear
- And I can see and undo hidden items from a Hidden page.

Quality attributes
- Persistence: hiding survives restarts and re-fetches.
- Isolation: hiding in my profile does not hide the job for my husband.

---

# Sprint 5 (v0.5): Stay on track and learn from results

## Feature 9: Notifications and daily summary

**How we achieve it:** APScheduler runs inside the app (or a small background command) to check reminders daily. Desktop notifications use the operating system's notification facility on macOS. The daily summary is built from the same "needs attention" query as Sprint 1, plus the count of new high-scoring jobs.

### Story 9.1: Get notified when something is due
*(Front)*
"As a job seeker, I want a desktop notification when a follow-up or deadline is due so that I do not miss it even when I do not have the app open."

*(Back)* Acceptance criteria

Functional
- Given an application with a follow-up date of today and notifications enabled
- When the scheduled morning check runs
- Then a desktop notification appears naming the company and the label
- And the same item does not trigger a second notification that day.

Quality attributes
- Reliability: if the computer was asleep at the scheduled time, the check runs on next wake or app start.
- Control: notifications can be turned off, and the time of day is configurable.

### Story 9.2: See a daily summary
*(Front)*
"As a user, I want a short summary each day of what is due, which applications went quiet and which new jobs match so that I can plan my time in a minute."

*(Back)* Acceptance criteria

Functional
- Given overdue follow-ups, applications with no response for 21 days, and five new jobs scoring above 70
- When I open the Today page
- Then I see three sections with those counts and links to the items.

Quality attributes
- Performance: the page loads in under 1 second.
- Configurability: the "no response" threshold (default 21 days) and the high-score threshold (default 70) can be changed in settings.

---

## Feature 10: Insights dashboard

**How we achieve it:** read-only SQL aggregates over applications and status events (counts by status, applications per week, time from Applied to first response). Charts are drawn in the web UI with a charting library. Response rate by resume version uses the resume-version link from Story 5.2.

### Story 10.1: See my funnel and weekly activity
*(Front)*
"As a job seeker, I want to see how many applications I send per week and how many reach each stage so that I know whether my search is on track."

*(Back)* Acceptance criteria

Functional
- Given 40 applications with varied statuses and dates
- When I open the Dashboard
- Then I see counts per status, a chart of applications per week, and the percentage that reached each stage.

Quality attributes
- Correctness: the numbers equal those from a direct database count (verified by a test with a fixed data set).
- Performance: the dashboard loads in under 2 seconds with 1,000 applications.

### Story 10.2: Learn which resume version gets responses
*(Front)*
"As a user who keeps several resume versions, I want response rates per version and per source so that I can put more effort into what works."

*(Back)* Acceptance criteria

Functional
- Given applications split between resume versions "Backend" and "General"
- When I open the "What works" section
- Then I see, for each version, how many applications were sent, how many got a response, and the response rate
- And a note appears when a group has fewer than 10 applications, saying the sample is too small to conclude.

Quality attributes
- Honesty: percentages are never shown without their counts.
- Correctness: "response" is defined once as any move out of Applied into Recruiter screen, Online assessment, Interview, Offer or Rejected. The definition is shown on the page and used everywhere.

---

## Out of this plan

Gmail sync, browser extension, resume tailoring, gap analysis, interview prep, networking and offer comparison are later work. They will get their own feature sections and stories when we plan the sprint they belong to.
