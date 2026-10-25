# InternIQ

An AI agent that finds, screens, and applies to internships for me.

InternIQ reads new Software Engineering postings on my university's job board, decides
which ones fit using my rules (and learns from my corrections), writes a tailored resume
and cover letter for each, fills out the employer's application site, and sends me a
Discord card so I can approve every submission with one tap.

Status: in development. See `BUILDORDER.md`.

Stack: Python, Playwright, Claude API (tool use), discord.py, SQLite.

## Setup (Windows)
```
py -3.12 -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env                              # then fill it in
copy config\answers.example.yaml config\answers.yaml
python -m interniq ping                             # posts "InternIQ is alive" in Discord
python -m playwright install chromium
```

Fonts for the resume and cover letter go in `data/base/fonts/`: Carlito (Regular, Bold,
Italic, BoldItalic) from Google Fonts and Roboto (Regular, Bold, Italic, BoldItalic).

## Commands
```
python -m interniq login              # log into Elevate once in the agent's browser
python -m interniq crawl              # save new ENSF postings and check employer links
python -m interniq screen             # apply / skip / ask for new postings, cards in Discord
python -m interniq decide skip <ids>  # answer review cards in bulk (apply, skip, or closed)
python -m interniq write <id> [ids]   # build resume + cover letter into data/output/<id>/
python -m interniq bot                # keep the Discord bot online (!status, !learning, buttons)
```
