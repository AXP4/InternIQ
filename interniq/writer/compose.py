"""Asks Claude to plan and write one application package.

Claude reads the posting plus Ali's rules, facts, letter template, and approved
example letters, then returns structured JSON: the term to state, the resume
focus, and the cover letter paragraphs. The big, unchanging context goes in a
cached system prompt, so only the posting is new on each call.
"""

import json
from datetime import date

import anthropic
from pydantic import BaseModel, Field
from pypdf import PdfReader

from interniq import config

RULES = config.ROOT / "RULES.md"
FACTS = config.ROOT / "config" / "profile_facts.md"
TEMPLATE = config.ROOT / "config" / "letter_template.md"
EXAMPLES = config.ROOT / "data" / "base" / "example_letters"

INSTRUCTIONS = """You write internship application packages for Ali Alanizi, a third year
Software Engineering student at the University of Calgary. For each posting you choose the
term he will state, the focus phrase for his resume profile line, and you write his cover
letter in his voice.

Follow RULES.md (screening context, term choice, resume profile line, letter writing rules),
the cover letter template, and the tone of the approved example letters. Claim only what is
in profile_facts.md. The example letters show tone and structure; never reuse a
company specific line from them.

Letter paragraphs: plain sentences, 4 or 5 paragraphs, fitting one page with the header
(about 330 to 400 words total). You may wrap a project name in <i></i> and the availability
phrase in the closing paragraph in <b></b>; use no other markup. Never use a dash of any kind
in the paragraphs, including hyphens in compound words. Where a dash would feel natural, write
two sentences instead of stringing clauses together with commas. The re_line and footer_label
keep the employer's official spelling of the job title (for example "Co-op"), minus the Elevate
program codes. The salutation and signature are added for you.

If anything about the posting is unclear (term options, a question the letter must answer,
a requirement Ali may not meet, an unusual file naming rule), still write the best package
you can and explain it in notes_for_ali."""


class Package(BaseModel):
    term_months: int = Field(description="Length of the term Ali will state, in months.")
    term_start: str = Field(description='Month and year, e.g. "May 2027".')
    term_end: str = Field(description='Month and year, e.g. "August 2028".')
    term_mismatch_reason: str = Field(description="Empty if the term matches an earlier application to this company (or there are none). Otherwise a short reason it differs, for Ali's approval card.")
    resume_focus: str = Field(description='Focus phrase for the resume profile line, e.g. "software development and data analytics".')
    recipient_team: str = Field(description='Bold first recipient line, e.g. "Hiring Team, DataHub" or "Hiring Team".')
    company_name: str = Field(description='Company as it should appear in the address block, e.g. "Pason Systems".')
    company_location: str = Field(description='City and province or country, e.g. "Calgary, AB".')
    re_line: str = Field(description='e.g. "Re: Software Developer Intern, DataHub (16 Months, May 2027 to August 2028)". Strip Elevate program codes like ENSF ENEL from the title.')
    footer_label: str = Field(description='Short company and role for the footer, e.g. "Pason Systems, Software Developer Intern (DataHub)".')
    paragraphs: list[str] = Field(description="The letter body paragraphs, 4 or 5.")
    learning_gaps: list[str] = Field(description="Tools or skills the posting asks for that Ali does not have, named in the letter as what he would be learning.")
    resume_file_name: str = Field(description='"Ali_Alanizi_Resume.pdf" unless the posting specifies a naming convention.')
    cover_letter_file_name: str = Field(description='"Ali_Alanizi_Cover_Letter.pdf" unless the posting specifies a naming convention.')
    notes_for_ali: list[str] = Field(description="Anything Ali should check or decide. Empty list if nothing.")


def example_letters() -> str:
    parts = []
    for pdf in sorted(EXAMPLES.glob("*.pdf")):
        text = "\n".join(page.extract_text() for page in PdfReader(pdf).pages)
        parts.append(f'<example_letter file="{pdf.name}">\n{text}\n</example_letter>')
    return "\n\n".join(parts)


def system_prompt() -> list[dict]:
    """Instructions plus all reference files, marked for prompt caching."""
    text = "\n\n".join([
        INSTRUCTIONS,
        f"<rules_md>\n{RULES.read_text(encoding='utf-8')}\n</rules_md>",
        f"<profile_facts_md>\n{FACTS.read_text(encoding='utf-8')}\n</profile_facts_md>",
        f"<letter_template_md>\n{TEMPLATE.read_text(encoding='utf-8')}\n</letter_template_md>",
        example_letters(),
    ])
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def posting_message(posting: dict, letter_wanted: bool, earlier: list[dict]) -> str:
    fields = {
        k: posting.get(k)
        for k in ["id", "title", "org", "division", "location", "term", "term_start", "duration",
                  "deadline", "procedure", "materials", "additional_info", "description", "qualifications"]
    }
    today = date.today()
    task = (
        "Write the package for this posting." if letter_wanted else
        "This posting does not ask for a cover letter, so don't write one: return an empty "
        "paragraphs list. Still choose the term and resume focus, and fill the other fields."
    )
    return (
        f"Today's date is {today:%B} {today.day}, {today:%Y}.\n\n"
        "Note: Elevate's list shows \"May 2027 - August 2028\" as the term for every posting; "
        "trust the posting's own start date, duration, and description instead.\n\n"
        f"<posting>\n{json.dumps(fields, indent=1, ensure_ascii=False)}\n</posting>\n\n"
        "Earlier applications to this company (context only; the term rules in RULES.md decide the "
        "term, and a difference is shown to Ali as a warning):\n"
        f"{json.dumps(earlier, indent=1, ensure_ascii=False) if earlier else 'none'}\n\n{task}"
    )


class Composer:
    """One conversation per posting, so fix requests keep the earlier draft in context."""

    def __init__(self, posting: dict, letter_wanted: bool = True, earlier: list[dict] | None = None):
        self.client = anthropic.Anthropic(api_key=config.require("ANTHROPIC_API_KEY"))
        self.system = system_prompt()
        self.messages = [{"role": "user", "content": posting_message(posting, letter_wanted, earlier or [])}]

    def _ask(self) -> Package:
        response = self.client.beta.messages.parse(
            model=config.WRITE_MODEL,
            max_tokens=16000,
            system=self.system,
            messages=self.messages,
            output_config={"effort": "high"},
            output_format=Package,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason in ("refusal", "max_tokens"):
            raise RuntimeError(f"Claude stopped early ({response.stop_reason}); no package written")
        self.messages.append({"role": "assistant", "content": response.content})
        return response.parsed_output

    def draft(self) -> Package:
        return self._ask()

    def fix(self, problems: list[str]) -> Package:
        self.messages.append({
            "role": "user",
            "content": "The automatic checks found these problems. Return the full corrected package.\n- "
                       + "\n- ".join(problems),
        })
        return self._ask()
