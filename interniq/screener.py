"""Decides apply / skip / ask for each new posting.

Claude Haiku reads the posting, RULES.md (the agent's judgment, in a cached system
prompt), and Ali's latest answers from Discord as examples. Postings Ali already
applied to, and postings whose link is dead, are handled without a model call.
"""

import json
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from interniq import config, db

RULES = config.ROOT / "RULES.md"
FACTS = config.ROOT / "config" / "profile_facts.md"

INSTRUCTIONS = """You screen internship postings for Ali Alanizi, a third year Software
Engineering student. Decide apply, skip, or ask using RULES.md section 1 and the past
decisions in section 6. A wrong apply costs Ali little; a wrong skip can lose him a job, so
skip only roles that are clearly not software. Mixed roles where a meaningful part is
software or data are apply with the "borderline" flag. Use ask when you genuinely can't
tell whether the software part is meaningful, or when the posting needs something Ali may
not have per profile_facts.md (security clearance, a license, a citizenship he lacks).

Not reasons to ask or skip:
- Missing skills or tools. Those are what Ali would learn; the cover letter names them.
- Elevate's standard note to apply on the company website and show "Expressed Intention
  to Apply". Every posting has it; it is not an unusual requirement.
- Duration, location, or how many roles the company posted (RULES.md "Never skip because of").

Also give form_term: the term Ali should pick if an application form forces a choice, per
RULES.md section 2. Choose only among the lengths and starts this posting actually offers:
the first offered length in the order 12, 16, 8, 4 months, with the January 2027 start if
offered. If the posting offers one fixed term, that is the answer. Use the posting's own
dates, not Elevate's term column, which always says May 2027 to August 2028. Write it like
"12 months, January 2027 to December 2027"."""

# "legal" marks anything Ali must confirm himself on the approval card (RULES.md section 5:
# export control, Controlled Goods, citizenship, background checks, attestations).
Flag = Literal["borderline", "international", "clearance", "citizenship", "license", "legal",
               "unusual_requirement", "verify_link"]


class Screening(BaseModel):
    decision: Literal["apply", "skip", "ask"]
    reason: str = Field(description="One or two plain sentences Ali can read on his phone.")
    flags: list[Flag] = Field(description="Empty list if none apply.")
    form_term: str = Field(description='e.g. "12 months, January 2027 to December 2027".')


def system_prompt() -> list[dict]:
    text = "\n\n".join([
        INSTRUCTIONS,
        f"<rules_md>\n{RULES.read_text(encoding='utf-8')}\n</rules_md>",
        f"<profile_facts_md>\n{FACTS.read_text(encoding='utf-8')}\n</profile_facts_md>",
    ])
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def posting_message(posting: dict, examples: list) -> str:
    fields = {k: posting.get(k) for k in [
        "title", "org", "division", "location", "term_start", "duration", "procedure",
        "description", "qualifications", "additional_info",
    ]}
    learned = "\n".join(
        f"- {e['org']}, {e['title']}: screener said {e['agent_decision']}, Ali said {e['ali_decision']}"
        for e in examples
    ) or "none yet"
    return (
        f"Ali's recent answers on Discord (they override RULES.md where they conflict):\n{learned}\n\n"
        f"<posting>\n{json.dumps(fields, indent=1, ensure_ascii=False)}\n</posting>"
    )


def screen_one(client: anthropic.Anthropic, posting: dict, examples: list, model: str) -> dict:
    """Ask the model about one posting. Returns a decision dict for db.save_decision."""
    response = client.messages.parse(
        model=model,
        max_tokens=2000,
        system=system_prompt(),
        messages=[{"role": "user", "content": posting_message(posting, examples)}],
        output_format=Screening,
    )
    if response.stop_reason in ("refusal", "max_tokens"):
        return {"decision": "ask", "reason": f"Screener stopped early ({response.stop_reason}).",
                "flags": [], "form_term": None, "model": model}
    return {**response.parsed_output.model_dump(), "model": model}


def decide(client: anthropic.Anthropic | None, posting: dict, examples: list, model: str) -> dict:
    """Rules that need no model first, then the model."""
    if posting.get("elevate_status") == "Expressed Intention to Apply":
        return {"decision": "applied", "reason": "Already applied on Elevate.", "model": None}
    if posting.get("link_status") == "dead":
        return {"decision": "closed", "reason": f"Employer link looks dead ({posting.get('link_note')}).",
                "model": None}
    decision = screen_one(client, posting, examples, model)
    if posting.get("link_status") == "verify" and "verify_link" not in decision["flags"]:
        decision["flags"].append("verify_link")
    return decision


def screen_new(model: str | None = None) -> list[dict]:
    """Screen every posting with status 'new'. Returns the results with posting info."""
    model = model or config.SCREEN_MODEL
    conn = db.connect()
    client = anthropic.Anthropic(api_key=config.require("ANTHROPIC_API_KEY"))
    examples = db.ali_examples(conn)
    results = []
    for row in db.postings_with_status(conn, "new"):
        posting = dict(row)
        decision = decide(client, posting, examples, model)
        db.save_decision(conn, posting["id"], decision)
        results.append({**decision, "posting": posting})
        print(f"  {posting['id']}  {decision['decision']:<7} {posting['org'][:24]:<24} {posting['title'][:50]}")
    return results
