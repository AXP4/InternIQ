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
  URL, then the page HTML), `link_status` (ok, dead, down, error, none) and
  `link_note` (why: HTTP 404, redirected to the job search page, "no longer
  accepting", site maintenance). "down" links are retried next crawl.
- Shared with the applier later, which uses the same site detection.

### 3. Screener (`interniq/screener.py`)
- Sends the posting text plus `RULES.md` plus Ali's past decisions
  (`decisions` table, used as examples) to Claude Haiku.
- Returns structured JSON: `{decision: apply|skip|ask, reason, resume_profile,
  term_choice, flags: [...] }`.
- **Learning mode** (on at first): every `skip` is sent to Ali on Discord to confirm.
  His answer is saved to `decisions` and fed back as an example next time. Ali can turn
  learning mode off with a Discord command once he trusts it.

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
  1. Read the current page's form fields (labels, types, options, required flags)
     from the page's accessibility tree.
  2. Send them to Claude Sonnet with the answer bank and the posting.
  3. Claude replies with tool calls: `fill_text`, `select_option`, `check_box`,
     `upload_file`, `click_next`, `ask_ali`.
  4. Python executes each call with Playwright, then repeats on the next page.
- Stops on the final review page. Takes a screenshot. Never clicks Submit by itself.
- Saves per-site notes (`site_notes` table) such as "Workday: resume autofill
  overwrites fields, re-check experience section" so it gets better over time.
- **Employer Email** postings: draft the email with attachments and send it to Ali for
  approval (send automatically only if Outlook API access works).

### 6. Discord bot (`interniq/discord_bot.py`)
- Private server, one channel per purpose: `#approvals`, `#questions`, `#log`.
- **Approval card:** company, role, location, term, deadline, files attached,
  screenshot of the final page, any legal answers to confirm, and anything the agent
  was unsure about. Buttons: ✅ Submit, ✏️ Fix (Ali types what to change), ❌ Skip.
- **Question card:** one question at a time with buttons or a text reply. Answers are
  saved to the answer bank so they are never asked twice.
- Commands: `!run`, `!status`, `!pause`, `!learning on|off`. Only messages from
  Ali's server are answered. `python -m interniq bot` keeps the bot online.
- Built so far: `!status` (posting counts by status, procedure, site, link status,
  deadlines in the next 7 days).

### 7. Scheduler
- Windows Task Scheduler runs `python -m interniq run` once a day (default 9 PM, set
  "wake the computer to run this task"). `!run` in Discord triggers it anytime.

## Data
SQLite `data/interniq.db` tables:
- `postings` (id, title, org, division, location, term, term_start, duration,
  deadline, procedure, link, email, materials, additional_info, description,
  qualifications, fields, raw_text, elevate_status, site, link_status, link_note,
  status, timestamps). Schema lives in `interniq/db.py`.
- `decisions` (posting_id, agent_decision, ali_decision, reason): the learning examples
- `applications` (posting_id, files, submitted_at, site, account_email, notes)
- `answers` (question_text, answer, source, last_used): mirror of the answer bank
- `site_notes` (site, note)

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
