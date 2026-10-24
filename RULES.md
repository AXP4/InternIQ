# InternIQ Rules

This file is the agent's judgment. The screener and writer read it on every run.
Ali edits it in plain English whenever his preferences change.

## 1. Screening: apply / skip / ask

### Apply when
- The work is **software**: software development, web or app development, firmware or
  embedded software, ML/AI, data engineering, data analytics with real programming
  (Python, SQL, Power BI), test automation, internal tools, IT systems development,
  automation (Power Automate, scripting).
- Mixed roles where a **meaningful part** is software or data (for example "write test
  system software" or "Python scripting and regression flows") also count. Mark them
  `apply` with a flag `borderline` so the approval card says so.

### Skip (auto-skip, no software)
Project management or project coordination, project controls, construction, drafting,
CAD design, CFD or fluid dynamics, chemical or process engineering, field operations,
geomatics, pipeline integrity, finance, sales, business analyst roles with no coding,
helpdesk or IT support with no development, pure hardware design (ASIC, analog,
layout, PCB, silicon validation), mechanical or civil design.

### Ask Ali when
- Unsure whether the software part is meaningful.
- The posting requires something Ali may not have (security clearance, a specific
  license, a citizenship requirement).
- A required document or question is unusual.
- **Learning mode is on:** ask before every skip.

### Never skip because of
- Duration. Any length is fine: 4, 8, 12, or 16 months.
- Location. Ali will move anywhere, in Canada or abroad. Flag international roles
  (visa needed; Ali is a Canadian citizen and can use International Experience Canada).
- How many roles the same company has. Apply to every role that fits, with no
  per-company limit.
- A start date of January or May 2027. Both are fine.
- Closed roles: if the employer link is dead or the posting says closed, mark `closed`
  and tell Ali.

## 2. Term choice
- Ali's preferred long term: **16 months, May 2027 to August 2028.**
- If the posting offers several lengths, pick the longest that fits and, if the form
  allows more than one choice, select every length Ali could do.
- January starts: use the posting's exact dates (for example January to April 2027,
  or January to August 2027).
- The resume profile line, cover letter, and form answers must all state the same term.

## 3. Resume profile line
Template:
> Dedicated Software Engineering student at University of Calgary seeking a {N} month
> internship in {focus}. Available {start month year} through {end month year}.

`{focus}` examples: "software development", "software development and data analytics",
"machine learning and software development", "AI, data, and software development",
"software development and test automation", "software development and automation".
Keep the profile to 2 lines.

## 4. Cover letter writing rules
- One page. Four or five short paragraphs.
- **No dashes of any kind** in the body (no em dash, en dash, or hyphen used as a dash).
  Write compound words without hyphens (for example "client side", "16 month").
- Sound like a real student, not AI. No "I am thrilled", "passionate about leveraging",
  "in today's fast paced world", "I am confident that". Short, direct sentences.
- Open with a specific reason this role or company fits Ali, not a generic opener.
- Use only facts from `config/profile_facts.md`. Never invent numbers, tools, or results.
- Name the posting's tools Ali does NOT have honestly, as "the parts I'd be learning".
- If the posting asks a question to answer in the cover letter, answer it explicitly
  (for example D-Pace's "Objective / Process / Outcome").
- Close with availability (term and dates), willingness to relocate if the job is
  outside Calgary, and "I'd welcome the chance to talk."
- Header: "ALI ALANIZI (legal name: Ali LEGALNAME)" and the contact line.

### AI workflow mention
- If the posting is AI positive (mentions AI assisted development, AI coding tools, Copilot,
  automation with AI, or exploring AI), mention that Ali builds with AI coding tools and frame
  it as a strength: he designs the architecture, plans the build order, decides features, and
  reviews, tests, and debugs everything himself.
- If the posting is neutral or doesn't mention AI, never mention the AI workflow. Just say he
  built the project.
- If the posting is AI negative (bans AI tools or stresses writing everything by hand), never
  mention it, and never list Claude, ChatGPT, or Gemini as skills in the letter.

### Quality checks (run on every letter before saving)
- No line may run past the page margin. If the Re: line is too long, shorten the job title or
  wrap it.
- Use "an" before 8, 11, and 18 ("an 8 month"), "a" before other numbers. This applies to the
  resume profile line too.
- If Ali already applied to the same company, use the same term as that application unless
  this posting doesn't offer it.
- Only write a cover letter if the posting requires or accepts one.
- Every listed qualification Ali actually has must be addressed in the letter (for example
  "strong interest in renewable energy" must mention renewable energy).
- Never describe the same project in two paragraphs.
- "The parts I'd be learning" names the top 2 or 3 tools only, never a long list.
- The opening says what Ali brings to the role, never just "they will train me."

## 5. Form answer rules
- Use `config/answers.yaml` first. If a question isn't there, ask Ali, then save it.
- Rate skills honestly. Never pick a level higher than the answer bank says.
- Program dropdown: "Software Engineering"; if missing, "Computer Science"; never
  "Information Technology".
- Legal and attestation questions: pre-fill from the answer bank but always show them
  on the approval card for Ali to confirm.
- Optional fields: leave empty unless the answer bank has a good answer.
- If the site parsed the resume into fields, check them against `resume.yaml` and
  fix wrong dates, titles, or merged text.

## 6. Past decisions (seed examples for the screener)
Apply:
- Pason Software Developer Intern (DataHub, EDR, QA, Enterprise Apps, Completions): software
- Qualcomm Firmware/Embedded Software: embedded C and Python tooling
- Qualcomm Low Power AI Software, ML Compiler and Performance: ML software
- AMD Graphics Software, Firmware, Long Term ML/AI: software
- AMD Hardware Design Verification: borderline, heavy Python scripting and regression flows
- AltaGas Digital (NextGen AI and Data): AI and data engineering
- AltaGas Transportation: Software Development stream (C#, Blazor, Azure)
- TAQA Enterprise Applications: AI, automation, proof of concepts
- Enbridge Power Performance: Python, SQL, Power BI, analytics software
- Enbridge Power Operations (Renewables): borderline, software and data tasks
- Enbridge Leak Detection: Python scripting, Power BI dashboards
- Enbridge SCADA: tools, HMI displays, automated alerts, build server jobs
- D-Pace Machine Learning Co-op: ML in Python on lab hardware
- Orennia Analytics Intern: datasets and analytical models in Python and SQL
- ATCO Gas Engineering Co-op: borderline, chose Process Improvements and Data Analysis scopes
- Airbus RF Test System Engineering: borderline, test system software in Java and agile sprints
- CNRL Developer Student, LodgeLink (Data Analyst, Integrations, Software), Circle
  Cardiovascular Software Development, Garmin, Hexagon, Pembina Leak Detection

Skip:
- Qualcomm Compute DSP/AI Processor: ASIC hardware design, Verilog required
- Qualcomm Silicon Validation, Physical Layout, Analog Design, MSIP, Display IP: hardware
- Enbridge PCSLD Co-op: project coordination and documentation
- TAQA Field Operations: oil field operations
- Harmac Process Engineering: pulp mill chemical process
- Hatch Project Controller: project controls
- Strathcona Engineering Co-op: SAGD field engineering support
- John Deere Agriculture and Turf: agriculture sales project
- Hitachi Energy CFD Simulations: CFD
- Tykans Junior PM / Business Analyst: project management, no coding
- EllisDon Project Coordinator: construction coordination
- Pason Electrical Design, Nutrien finance roles: not software
