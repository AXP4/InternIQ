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

## Phase 4: Screener with learning mode ✅
- ✅ Screening prompt, JSON output, decisions table.
- ✅ Discord question cards for every skip (learning mode) and every "ask".
- ✅ Measure agreement with Ali (see notes: his skip reviews became the answer key).
- ✅ **Done when:** agreement is 90% or higher, and Discord questions work.
- Notes:
  - Deviation: instead of a hand labeled test set, Ali's reviews of the screener's skips
    are the answer key, because a wrong apply costs little and every skip is reviewed
    anyway. First run: Ali agreed with 11 of 11 skips (100%); he also decided the 5 asks
    (4 skip, 1 apply) and the dead links.
  - Model: compared Haiku 4.5 and Haiku 5.5 on all postings. Haiku 5.5 followed the
    rules better (skips only clear non software roles, doesn't ask over missing skills)
    and is 10x cheaper, so screening uses `claude-haiku-5-5`.
  - The screener also reads `profile_facts.md` (license, citizenship) and returns the
    form term choice and flags (`borderline`, `legal`, `verify_link`, ...).
  - Buttons carry the posting ID in their custom ID, so cards work after a bot restart.
    `python -m interniq decide <apply|skip|closed> <ids>` saves answers in bulk and
    updates the matching cards.
  - Link check fixes found while reviewing: a redirect to a search page now triggers a
    careers site search (title plus city) and becomes "verify" instead of dead; Employer
    Email and Accumulate Online postings get no link check.
  - The screener's output varies a little between runs (Baytex HSE flipped between dry
    run and real run), which is why skips stay reviewed while learning mode is on.

## Phase 5: Discord approval flow ✅
- ✅ Approval cards with buttons, Fix flow, Skip flow, commands.
- ✅ **Done when:** Ali can approve, fix, or skip a dummy application from his phone.
- Notes:
  - Cards go to `#approvals` with the PDFs attached: company, role, location, deadline,
    procedure and site, the form term, legal questions (`legal` flag), other flags,
    unresolved check problems, earlier fix requests, and the writer's notes (trimmed
    first to fit Discord's 2000 character limit).
  - Submit sets the application to `approved` (the gate the applier will require); the
    actual submission comes in Phase 6. Skip also saves a skip decision for the screener.
  - Fix opens a text box; the writer revises the previous draft with Ali's request (in a
    background thread so the bot stays responsive) and posts a new card.
  - New `applications` table tracks pending, fixing, approved, skipped, submitted.
  - `python -m interniq propose <ids>` posts approval cards, writing missing packages.
  - `!run` and `!pause` drive the daily run, so they move to Phase 8.
  - Tested on two dummy postings from Ali's phone (Submit, Skip, Fix); dummies deleted.

## Phase 6: Applier, first site type ✅
- Start with **Workday** (used by AltaGas, Airbus, and many others).
- Account creation, form reading, the fill loop with tool calls, uploads, stop at
  the review page, screenshot, approval card, submit on ✅.
- Click "I intend to apply" on Elevate after a successful submit.
- Term questions on forms follow RULES.md section 2 (12, 16, 8, then 4 months; January
  2027 start first; select every term Ali could do when several are allowed).
- If a site has an optional cover letter upload for a posting that didn't ask for one, ask
  Ali whether to write one.
- Notes:
  - ✅ First live application: AltaGas 2027 Engineering Co-op (152491, Workday R7307),
    submitted by the agent on 2026-10-25 after Ali's Submit tap, with resume, cover letter,
    and transcript; then "I intend to apply" clicked on Elevate and confirmed.
  - Dry runs fill a decoy job on the same Workday site (AltaGas R7346) and stop at Review.
  - Workday quirks learned (saved as site notes too): Apply Manually, never autofill; the
    hidden `beecatcher` field is a honeypot; Next is `pageFooterNextButton` (or
    `bottom-navigation-next-button`); a saved draft shows Continue Application; "Something
    went wrong" after sign in needs one refresh; date inputs sit under a display layer;
    open dropdown options render outside the question containers.
  - Every action re-reads its field to confirm the value stuck. A bug found live: the file
    reader only saw the first file in a multi-file field, so a good cover letter upload
    looked failed. Fixed, plus a Review page check that every expected file is listed
    before the approval card is posted.
  - The job title is read from the application page and must match the posting before
    anything is filled; the browser stays on Review until Ali decides.
  - Submitting is gated twice: no submit tool for Claude, and `workday.submit()` refuses
    unless the database says approved.
- **Done when:** one real Workday application goes through end to end with Ali approving.

## Phase 7: More site types
- Add, one at a time: iCIMS, SuccessFactors, Greenhouse, Oracle, UltiPro, Eightfold,
  Raise (Enbridge), simple custom sites. Save site notes as they're learned.
- ✅ **iCIMS** (`applier/icims.py`), tested on AMD: 8 applications submitted in two
  sessions with one login each, every one approved by Ali, plus Elevate intent.
  - The portal lives in an iframe (`in_iframe=1`); step 4 forms sit in a second, nested
    frame. The current step comes from the progress bar (`.iCIMS_Steps_Current`), and
    only the last step's Submit sends the application, so `next_page` refuses there.
  - Sign in: email plus privacy consent, then hCaptcha (Ali solves it) and the
    login.icims.com password page. Only the remembered password slot is tried, and a
    passkey prompt is declined. If anything looks stuck, Ali gets a screenshot and takes over.
  - iCIMS reopens real jobs at step 3, so the shared profile resume is replaced first
    through "Update Your Profile". One-file upload fields refuse a second file (a cover
    letter once replaced the resume).
  - `apply <ids...>` runs a batch in one browser session. Duplicate postings for the same
    job get the Elevate intent after the one submit.
  - Approval cards now carry `answers.txt` (every answer and where it came from, with ⚠️
    for required fields left empty) and a screenshot of each filled step.
  - Skill levels come only from the answer bank; otherwise Claude asks once per page.
    It never picks the lowest option when the bank says "none".
  - Bug found live: a late Submit tap on an old card reset a submitted application to
    approved. Submitted rows are now final.
- Employer Email postings: draft emails for approval.
- ✅ "Accumulate Online" postings: apply inside Elevate itself (`elevate/apply.py`), same
  approval flow. Moved ahead of the other sites because of deadlines; 7 submitted on
  2026-10-26 with Ali approving each.
  - APPLY only opens the package form. Tailored PDFs are uploaded to Ali's Elevate document
    library (`Ali_Alanizi_Resume_<id>`), then a custom package is built from them.
  - No Claude loop: the form is always the same few dropdowns.
  - After approval the form is rebuilt from scratch (the session may expire while Ali
    decides), compared with what he approved, then submitted and confirmed in the
    Application Submitted list.
  - Postings that want everything "in one document" get one merged PDF (letter, resume,
    transcript). The transcript is AES encrypted, hence the `cryptography` dependency.
- **Done when:** every ATS seen in the current ENSF backlog is handled or cleanly
  handed to Ali.

## Phase 8: Daily automation
- Task Scheduler job (daily 9 PM, wake from sleep), `!run` and `!pause` commands, crash recovery
  (resume from saved status), log cleanup.
- **Done when:** a full unattended night run processes new postings and only pings
  Ali for approvals and questions.

## Phase 9: Polish for the portfolio
- README with a short demo GIF, architecture diagram, and the accuracy numbers from
  Phase 4. Double check git history has no personal data, then make the repo public.
