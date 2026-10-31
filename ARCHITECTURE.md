# InternIQ Architecture

## The big picture
Think of InternIQ as a small team of workers passing a folder down a line:

```
 Elevate          Screener        Writer          Applier          Ali
 Crawler   --->   (Claude)  --->  (Claude)  --->  (Playwright  ---> (Discord
 (reads jobs)     apply/skip/ask  resume+letter    + Claude)        approve)
                                                   fills forms
                        \__________ SQLite database (the shared folder) __/
```

Each worker does one job, writes its result to the database, and the next worker picks
it up. If something breaks halfway, the agent can resume from the last saved status.

## Components

### 1. Browser session (`interniq/browser.py`)
- Playwright Chromium with a **persistent profile** stored in `data/browser_profile/`
  (gitignored). Ali logs into Elevate (UCalgary SSO + 2FA) once in this window; the
  login is saved like a normal browser.
- Always **headed** (visible window) so Ali can solve CAPTCHAs or 2FA when asked.
- Elevate forgets its session when the browser closes, so each run clicks "Student
  Login"; the saved Microsoft SSO login usually completes it with no input. If it
  doesn't within 30 seconds, ping Ali on Discord: "Log in to Elevate".
- `python -m interniq login` opens Elevate in this browser for a manual login.

### 2. Elevate crawler (`interniq/elevate/`)
- Start page: `https://elevate.ucalgary.ca/myAccount/engineering/jobs-engineering.htm`
- Click the **ENSF Jobs** quick search, then sort by **App Deadline** (click the column
  header) so the nearest deadlines come first.
- Results table columns: App Status, Term, ID, Job Title, Organization, Division,
  Position Type, Openings, Location, App Deadline. Each row has Shortlist and Apply
  buttons. "Apply" on Elevate means "I intend to apply"; after clicking, the row shows
  "Expressed Intention to Apply".
- For each posting ID not already in the database, open the posting and save the
  full text plus these fields: work term start, duration, job title, organization,
  location, description, qualifications, deadline, application procedure
  ("Employer Website", "Employer Email", or "Accumulate Online" = apply inside
  Elevate), application link or email, required materials (resume, cover letter,
  transcript, other), and "Additional Application Information". Every label/value
  pair on the page is also kept as JSON (`fields`), so unusual fields aren't lost.
- Postings open in a new tab via a form POST (no stable URL); the crawler clicks the
  job title in the results row and refuses to click anything labelled Apply,
  Shortlist, or Not Interested.
- Wait 2 to 5 seconds between pages.
- `python -m interniq crawl` runs the crawl plus link check and posts a summary to `#log`.

### 2b. Link checker (`interniq/sites.py`)
- Opens each new employer link once. Records the site type (from the URL, the final
  URL, then the page HTML), `link_status` (ok, dead, verify, down, error, none) and
  `link_note` (why: HTTP 404, "no longer accepting", site maintenance, job moved).
  "down" links are retried next crawl.
- Dead only on a 404 or a page that says the job is gone. A redirect to a search page
  triggers a search of the careers site by title; a match in the posting's city replaces
  the link, otherwise the status is "verify" (check manually).
- Employer Email and Accumulate Online postings are not checked (status "none").
- Shared with the applier later, which uses the same site detection.

### 3. Screener (`interniq/screener.py`)
- Sends the posting plus `RULES.md` and `config/profile_facts.md` (cached system prompt)
  and Ali's latest Discord answers (`decisions` table, as examples) to Claude Haiku 5.5.
- Returns structured JSON: `{decision: apply|skip|ask, reason, flags: [...], form_term}`.
  Leans toward apply: a wrong apply costs little, a wrong skip can lose a job.
- Handled without a model call: postings Ali already applied to on Elevate (`applied`)
  and postings with a dead link (`closed`). A `verify` link adds the `verify_link` flag.
- **Learning mode** (on at first): every `skip`, every `ask`, and every dead link gets a
  card in `#questions` with Apply / Skip buttons. Ali's answer is saved to `decisions`,
  updates the posting's status, and becomes an example next time. `!learning off` stops
  skip cards (asks and dead links still come).
- `python -m interniq screen` screens every `new` posting; `python -m interniq decide`
  saves answers in bulk and updates the cards.

### 4. Writer (`interniq/writer/`)
- **Resume (`resume.py`):** built from structured data (`config/resume.yaml`) into a PDF
  that matches Ali's master resume (spacing measured from it). Only the profile line
  changes per job: the focus phrase, from the list in `RULES.md` section 3. Availability is
  always "January 2027 through August 2028". Never edit the old PDF with overlays.
- **Cover letter (`compose.py` + `letter.py`):** Claude Sonnet returns structured JSON
  (focus, the posting's AI stance, Re: line, paragraphs, learning gaps, notes for Ali)
  using `RULES.md`, `config/profile_facts.md`, `config/letter_template.md`, and the four
  approved letters in `data/base/example_letters/`, all in one cached system prompt.
  `letter.py` renders it in the approved design. A letter is only written when the
  posting asks for one.
- **Transcript:** copied unchanged from `data/base/transcript.pdf` when required.
- **File names:** default `Ali_Alanizi_Resume.pdf` and `Ali_Alanizi_Cover_Letter.pdf`;
  if the posting specifies a naming convention, follow it exactly.
- **Checks (`checks.py`):** no dashes or generated sounding phrases, 4 or 5 paragraphs,
  exact closing sentence (relocation clause only outside Calgary), no term length or date
  past August 2028, Re: line without a term, no lines past the margin, one page, allowed
  focus phrase, no company names copied from the example letters. Failures go back to
  Claude to fix, up to 2 rounds.
- **Output (`package.py`):** `data/output/<posting_id>/` holds the PDFs and `package.json`
  (the plan, notes for Ali, and any unresolved problems).
- Fonts: `data/base/fonts/` (Carlito, Roboto) plus Windows Arial Bold.

### 5. Applier (`interniq/applier/`)
- Opens the employer link from the posting. Detects the application system
  (Workday, iCIMS, SuccessFactors, Greenhouse, Oracle, UltiPro, Eightfold, Raise,
  custom) from the URL and page.
- Creates an account if needed with the email and password pattern from `.env`.
  Verification codes: try the Outlook API (Microsoft Graph); if UCalgary blocks it,
  ask Ali on Discord to paste the code.
- **Form filling loop** (this is the "agent" part):
  1. The site driver (`workday.py`, `icims.py`, `avature.py`, `eightfold.py`,
     `greenhouse.py`; each has the same function names, so `run.py` picks one from the
     site) reads every visible question on the page: label,
     kind (text, select, search select, radio, checkbox, date, file), options, required
     flag, and current value, and tags each with a stable id.
  2. `filler.py` sends them to Claude Sonnet with RULES.md, profile facts, resume.yaml,
     the answer bank (`answers.yaml` + `learned_answers.yaml`), and the site notes.
  3. Claude replies with tool calls: `fill_text`, `select_option`, `select_search`,
     `choose_radio`, `set_checkbox`, `set_date`, `upload_file`, `press_add`, `read_page`,
     `ask_ali`, `record_legal`, `record_unsure`, `save_site_note`, `page_done`. There is
     no submit tool.
  4. Python runs each call and re-reads the field to confirm the value stuck; mismatches
     and the real option lists go back to Claude. Then Save and Continue; Workday's
     error messages go back to Claude (2 retries per page).
- `ask_ali` posts in `#questions`, waits for his reply, and appends it to
  `config/learned_answers.yaml` (gitignored) so it's reused.
- Before filling, the job title on the page must match the posting. At Review: check
  every expected file is listed, screenshot, post the approval card, and keep the page
  open until Ali decides. `submit()` in every driver refuses unless the application is
  approved. The card also carries `answers.txt` (each answer and its source) and a
  screenshot of every filled step.
- iCIMS specifics: the form sits in nested iframes, steps are read from the progress bar,
  and the shared profile resume is replaced before each job because iCIMS reopens jobs
  past the resume step. A batch (`apply <ids...>`) shares one sign-in.
- One-page forms (Eightfold, Greenhouse) are their own final page: filled once, then the
  card. Avature (EA) treats any page it can't place in the step bar as final.
- **Fix on a form card:** while a run has the form open behind a card (`live_form:<id>`
  setting), Fix means "change this answer": the run hands Ali's note to the filler, re-reads
  the page, and posts a new card. Without a live form, Fix rewrites the resume and letter.
- **Approval 2:** `submit()` presses the final button exactly once. If the site shows
  another page with questions instead of a confirmation (EA's extra pages, Greenhouse's
  emailed security code), it's filled (asking Ali when needed) and gets its own approval
  card before the next press.
- Safety checks before anything is saved: form name fields hold the legal name (Ali
  LEGALNAME; "Alanizi" only in preferred-last-name fields), the page's job title
  matches the posting (term words like Fall/Spring must agree), and parser-filled skill
  tags are replaced with profile facts only.
- After a confirmed submit, `elevate/intent.py` clicks "I intend to apply" on Elevate and
  confirms the row shows "Expressed Intention to Apply". If Elevate is blocked, the click
  is queued; `intend` (no id) does every queued click in one visit.
- **Elevate security block:** a block page stops all Elevate work at once (`ElevateBlocked`)
  and sets `elevate_blocked`; nothing touches Elevate again until Ali runs `unblock`.
  Bot checks and blocks are never worked around.
- Saves per-site notes (`site_notes` table) such as "a saved draft shows Continue
  Application" so it gets better over time.
- Commands: `dryrun <id> <url>` (fill a decoy job on the same site, stop at Review),
  `apply <ids...>`, `email <ids...>`, `intend [id]`, `unblock`.
- **Accumulate Online** postings (`elevate/apply.py`): no employer site. The tailored PDFs
  go into Ali's Elevate document library, the APPLY form gets a custom package built from
  them, and Submit Application is pressed only after approval, then confirmed in Elevate's
  Application Submitted list. `apply <ids...>` picks this path from the posting's procedure.
- **Employer Email** postings (`applier/email_apply.py`, `email <ids...>`): the agent writes
  the package and a short email (no dashes, no contractions), and the card shows To,
  Subject, Body and the PDFs. Ali sends it from his university Outlook (the agent has no
  access to it); on an email card, Submit means "I sent it" and records the submission.

### 6. Discord bot (`interniq/discord_bot.py`)
- Private server, one channel per purpose: `#approvals`, `#questions`, `#log`.
- **Approval card:** company, role, location, term, deadline, files attached,
  screenshot of the final page, any legal answers to confirm, and anything the agent
  was unsure about. Buttons: ✅ Submit, ✏️ Fix (Ali types what to change), ❌ Skip.
- **Question card:** one question at a time with buttons or a text reply. Answers are
  saved to the answer bank so they are never asked twice.
- Commands: `!run`, `!status`, `!pause`, `!learning on|off`. Only messages from
  Ali's server are answered. `python -m interniq bot` keeps the bot online.
- Built so far: `!status` (posting counts, screener agreement on skips, learning mode,
  procedures, sites, links), `!learning on|off`, review cards in `#questions`, and
  approval cards in `#approvals` (PDFs attached; Submit sets `approved`, Fix revises the
  package from Ali's text and posts a new card, Skip records a skip).
- All buttons carry the posting ID in their custom ID (`decide:...`, `approval:...`), so
  they keep working after the bot restarts.

### 7. Scheduler
- Windows Task Scheduler runs `python -m interniq run` once a day (default 9 PM, set
  "wake the computer to run this task"). `!run` in Discord triggers it anytime.

## Data
SQLite `data/interniq.db` tables:
- `postings` (id, title, org, division, location, term, term_start, duration,
  deadline, procedure, link, email, materials, additional_info, description,
  qualifications, fields, raw_text, elevate_status, site, link_status, link_note,
  status, timestamps). Schema lives in `interniq/db.py`.
- `decisions` (posting_id, agent_decision, reason, flags, form_term, model,
  ali_decision, timestamps): the learning examples and the agreement score
- `settings` (key, value): runtime switches such as learning mode
- `applications` (posting_id, state pending/fixing/approved/skipped/submitted, files,
  fix_requests, site, account_email, notes, timestamps). Only Ali's Submit sets `approved`.
- `answers` (question_text, answer, source, last_used): mirror of the answer bank
- `site_notes` (site, note): quirks learned per application system

## Folder layout
```
InternIQ/
  README.md  ARCHITECTURE.md  BUILDORDER.md  RULES.md
  .env.example          -> copy to .env (gitignored)
  requirements.txt
  config/
    answers.example.yaml  -> copy to answers.yaml (gitignored)
    profile_facts.md      (gitignored, personal)
    letter_template.md    (gitignored, personal)
    resume.yaml           (gitignored, personal; Phase 3)
  data/                 (gitignored)
    base/  transcript.pdf, master resume, example_letters/, fonts
    output/<posting_id>/
    browser_profile/  logs/  interniq.db
  interniq/             (the code)
  reference/            (gitignored, old manual workflow scripts for design reference)
```

## Costs
Claude API about $5 to $20 per month at Ali's volume. Haiku for screening keeps the
cost low; Sonnet only for letters and forms.
