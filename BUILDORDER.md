# InternIQ Build Order

Rule: finish, test, explain, and commit each phase before starting the next. Mark steps ✅
as they land and add a short note when something deviates from the plan. Each phase ends
with something Ali can run and see working.

## Phase 1: Setup and accounts ✅
- ✅ `git init`, confirm `.gitignore` covers all personal files, create
  the GitHub repo `AXP4/InternIQ` (private until Phase 9), set `origin`, first commit
  and push.
- ✅ Create the code structure from `ARCHITECTURE.md`, virtual env, `requirements.txt`.
- ✅ Walk Ali through: getting an Anthropic API key, creating a private Discord server,
  creating a Discord bot in the Developer Portal, inviting it, and filling `.env`.
- ✅ **Done when:** `python -m interniq ping` posts "InternIQ is alive" in Discord.
- Notes: the venv uses Python 3.12 (`py -3.12 -m venv .venv`) alongside other installed
  versions. Ping posts to `#log`. The Anthropic API key expires Sep 1, 2027.

## Phase 2: Elevate crawler (read only) ✅
- ✅ Persistent Playwright browser; Ali logs into Elevate once.
- ✅ Open ENSF Jobs, sort by App Deadline, collect every row, open each posting, save
  all fields and raw text to SQLite. Follow each application link once and record
  the site type and whether the page still exists (catches dead links).
- ✅ **Done when:** the database holds every current ENSF posting with complete fields,
  and `!status` in Discord shows counts.
- Notes:
  - Elevate's session cookie dies when the browser closes, but the Microsoft SSO login
    persists, so the crawler clicks "Student Login" each run and gets in automatically.
    It pings `#questions` only if Microsoft asks for a password or 2FA again.
  - Sorting by deadline happens in Python instead of clicking the column header (one
    fewer page load, same order). All ENSF results fit on one page, no pagination.
  - Postings open in a new tab through a form POST, so they can't be revisited by URL;
    the crawler clicks the job title from the results list.
  - Link check records `link_status` (ok, dead, down, error, none) and a `link_note`
    explaining why. "down" (site maintenance) is retried on the next crawl.
  - First run: 63 postings, 39 Employer Website, 14 Accumulate Online (apply inside
    Elevate), 10 Employer Email. Sites seen: iCIMS, Greenhouse, Workday, Avature,
    SuccessFactors, Oracle, Eightfold, ADP, Raise, Oleeo, Dayforce, BambooHR, custom.

## Phase 3: Resume and cover letter generator ✅
- ✅ Convert Ali's resume into `config/resume.yaml` and rebuild it as a PDF that looks
  like his current one (centered name, legal name line, same sections).
- ✅ Port the cover letter design from `reference/letter_lib.py`.
- ✅ Claude writes letter paragraphs from `profile_facts.md` and `RULES.md`.
- ✅ Automatic checks: no dashes in letter body, one page, correct names and dates.
- ✅ **Done when:** Ali gives 3 real postings and is happy with all 3 packages
  (South Bow 152720, SeisWare 152801, AMD 152559).
- Notes:
  - Fonts (Carlito for the resume, Roboto for the letter) live in `data/base/fonts/`; the
    resume name uses Windows Arial Bold, which matches the original Liberation Sans Bold.
  - Ali added rules while reviewing the samples: AI workflow mention by posting stance,
    margin check, letter only when the posting asks for one, top 3 learning gaps, and a
    fixed resume focus phrase list.
  - Availability changed from a per posting term to one window: documents never state a
    term length, only "January 2027 through August 2028", with an exact closing sentence.
    Term choice moved to the applier (forms that force one).
  - Elevate's Term column shows "May 2027 - August 2028" on every posting; the real term is
    in the posting's own fields and description.
  - `python -m interniq write <id> ...` builds a package into `data/output/<id>/`.

## Phase 4: Screener with learning mode
- Screening prompt, JSON output, decisions table.
- Discord question cards for every skip (learning mode) and every "ask".
- Build a test set from Ali's past decisions (the postings already screened by hand:
  applies and skips) and measure agreement, like CourseIQ's answer key.
- **Done when:** agreement on the test set is 90% or higher, and Discord questions work.

## Phase 5: Discord approval flow
- Approval cards with buttons, Fix flow, Skip flow, commands.
- **Done when:** Ali can approve, fix, or skip a dummy application from his phone.

## Phase 6: Applier, first site type
- Start with **Workday** (used by AltaGas, Airbus, and many others).
- Account creation, form reading, the fill loop with tool calls, uploads, stop at
  the review page, screenshot, approval card, submit on ✅.
- Click "I intend to apply" on Elevate after a successful submit.
- Term questions on forms follow RULES.md section 2 (12, 16, 8, then 4 months; January
  2027 start first; select every term Ali could do when several are allowed).
- If a site has an optional cover letter upload for a posting that didn't ask for one, ask
  Ali whether to write one.
- **Done when:** one real Workday application goes through end to end with Ali approving.

## Phase 7: More site types
- Add, one at a time: iCIMS, SuccessFactors, Greenhouse, Oracle, UltiPro, Eightfold,
  Raise (Enbridge), simple custom sites. Save site notes as they're learned.
- Employer Email postings: draft emails for approval.
- "Accumulate Online" postings (14 in the first crawl): apply inside Elevate itself, same
  approval flow. Left for the end by Ali's decision, along with other odd cases.
- **Done when:** every ATS seen in the current ENSF backlog is handled or cleanly
  handed to Ali.

## Phase 8: Daily automation
- Task Scheduler job (daily 9 PM, wake from sleep), `!run` command, crash recovery
  (resume from saved status), log cleanup.
- **Done when:** a full unattended night run processes new postings and only pings
  Ali for approvals and questions.

## Phase 9: Polish for the portfolio
- README with a short demo GIF, architecture diagram, and the accuracy numbers from
  Phase 4. Double check git history has no personal data, then make the repo public.
