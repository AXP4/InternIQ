# InternIQ Build Order

Rule: finish, test, explain, and commit each phase before starting the next. Mark steps ✅
as they land and add a short note when something deviates from the plan. Each phase ends
with something Ali can run and see working.

## Phase 1: Setup and accounts
- `git init`, confirm `.gitignore` covers all personal files, create
  the GitHub repo `AXP4/InternIQ` (private until Phase 9), set `origin`, first commit
  and push.
- Create the code structure from `ARCHITECTURE.md`, virtual env, `requirements.txt`.
- Walk Ali through: getting an Anthropic API key, creating a private Discord server,
  creating a Discord bot in the Developer Portal, inviting it, and filling `.env`.
- **Done when:** `python -m interniq ping` posts "InternIQ is alive" in Discord.

## Phase 2: Elevate crawler (read only)
- Persistent Playwright browser; Ali logs into Elevate once.
- Open ENSF Jobs, sort by App Deadline, collect every row, open each posting, save
  all fields and raw text to SQLite. Follow each application link once and record
  the site type and whether the page still exists (catches dead links).
- **Done when:** the database holds every current ENSF posting with complete fields,
  and `!status` in Discord shows counts.

## Phase 3: Resume and cover letter generator
- Convert Ali's resume into `config/resume.yaml` and rebuild it as a PDF that looks
  like his current one (centered name, legal name line, same sections).
- Port the cover letter design from `reference/letter_lib.py`.
- Claude writes letter paragraphs from `profile_facts.md` and `RULES.md`.
- Automatic checks: no dashes in letter body, one page, correct names and dates.
- **Done when:** Ali gives 3 real postings and is happy with all 3 packages.

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
- **Done when:** one real Workday application goes through end to end with Ali approving.

## Phase 7: More site types
- Add, one at a time: iCIMS, SuccessFactors, Greenhouse, Oracle, UltiPro, Eightfold,
  Raise (Enbridge), simple custom sites. Save site notes as they're learned.
- Employer Email postings: draft emails for approval.
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
