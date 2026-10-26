"""Fills one application page: Claude decides, Python acts and verifies.

Claude sees the page's questions and answers with tool calls (fill_text, select_option,
ask_ali, ...). Each tool runs one of the site driver's verified actions and reports what
actually happened, so Claude can correct anything that didn't stick. There is no submit
tool: submitting happens elsewhere, and only after Ali approves.
"""

import json

import anthropic

from interniq import config, db
from interniq.applier import answers
from interniq.applier import workday as wd

RULES = config.ROOT / "RULES.md"
FACTS = config.ROOT / "config" / "profile_facts.md"
RESUME = config.ROOT / "config" / "resume.yaml"
MAX_TURNS = 40

INSTRUCTIONS = """You fill out online job applications for Ali Alanizi, one page at a time,
using tools. You see the page's questions as JSON (id, label, kind, options, current
value, required). Fill every required question and every optional one the answer bank
covers well; leave other optional questions empty.

Where answers come from, in this order: learned_answers.yaml (Ali's own words, newest
wins), answers.yaml, resume.yaml, profile_facts.md, and the posting. Never invent a fact,
a skill level, or an answer. If a required question isn't covered, ask Ali.

Skill or experience levels: answer ONLY from answers.yaml skill_levels, learned answers, or
a skill profile_facts.md lists by name (its "None" list means no exposure). Never infer a
level from related skills, and never default to "no exposure". Every other skill level must
be asked.

Asking: before filling a page, collect EVERY question on it you can't answer from those
sources and ask them all in ONE ask_ali call, numbered, each with its options, e.g.
"1. Tcl (No exposure / Basic / Intermediate / Advanced)  2. UVM (...)". Ali replies once
("1 basic, 2 none"). Then fill everything. Only ask again if his reply is unclear.
Follow RULES.md section 5 (form answers) and section 2 (term choice: use form_term).

Legal and attestation questions (citizenship, work authorization, export control,
Controlled Goods, background check consent, criminal record, security clearance,
"I certify", "I agree", terms of use): fill them from the answer bank if it has the
answer, otherwise ask Ali, and ALWAYS call record_legal for each one so Ali confirms it on
the approval card. Voluntary self-identification questions (gender, ethnicity,
disability, veteran status): use the answer bank, or ask Ali once.

How to work:
- You may call several tools at once. Every tool returns what actually happened; if a
  value didn't stick or an option didn't match, fix it (the error lists the real options).
- Pick dropdown options by meaning, not wording. If no option is honestly right, ask Ali.
  To clear a dropdown, call select_option with an empty value.
- Term questions ("preferred length of your co-op term", start term, ...): pick from the
  options offered in this order: 12 months, then 16, then 8, then 4, with the January 2027
  start when offered (answers.yaml availability). Never answer "none of these".
- Ali's own email and phone go in every field asking for HIS contact details, exactly as in
  answers.yaml. Referral "Source" fields (source email, source phone, referrer) are about
  who referred him: leave them empty unless the answer bank names a referrer.
- Repeating sections (work experience, education, websites): press the section's Add
  button, then fill the new fields from resume.yaml / answers.yaml. Never add more entries
  than the source data has.
- Upload the resume FIRST, before filling anything else on that page: some sites parse it
  and re-fill other fields. Then read_page and correct whatever the parser filled.
- Work or professional experience entries are jobs only (resume.yaml WORK EXPERIENCE and
  VOLUNTEER EXPERIENCE). Projects are not employers: if a parser put projects in there,
  rewrite those entries as Ali's real jobs, and leave any extra entries empty.
- Documents: upload the resume to the resume question and the transcript wherever one is
  asked for. If the posting's materials include a cover letter and the form has no
  separate cover letter question, upload the cover letter next to the resume in the
  Resume/CV question (it keeps both when the field accepts several files; the tool tells
  you if it doesn't, and then record_unsure instead).
- Call record_unsure for anything you're not fully sure about, so it shows on the card.
- Call save_site_note when you learn a quirk of this application system worth
  remembering for every future application (not posting specific details).
- Never touch a field named beecatcher. You cannot submit; when every question on the
  page is handled, call page_done."""


def _tool(name: str, description: str, props: dict) -> dict:
    return {"name": name, "description": description, "strict": True,
            "input_schema": {"type": "object", "properties": props, "required": list(props),
                             "additionalProperties": False}}


S = {"type": "string"}
FIELD = {"type": "string", "description": "The question's id from the page JSON, e.g. f12"}
TOOLS = [
    _tool("fill_text", "Type into a text or textarea question.", {"field_id": FIELD, "value": S}),
    _tool("select_option", "Pick an option in a dropdown (kind select).", {"field_id": FIELD, "value": S}),
    _tool("select_search", "Search and pick in a searchable prompt (kind multiselect).", {"field_id": FIELD, "value": S}),
    _tool("choose_radio", "Pick one radio choice.", {"field_id": FIELD, "value": S}),
    _tool("set_checkbox", "Tick or untick a checkbox. option: the box's label in a group, or empty for a single box.",
          {"field_id": FIELD, "checked": {"type": "boolean"}, "option": S}),
    _tool("set_date", "Fill a date question. Use numbers; leave a part empty if the field doesn't have it.",
          {"field_id": FIELD, "month": S, "day": S, "year": S}),
    _tool("upload_file", "Upload one of Ali's documents to a file question.",
          {"field_id": FIELD, "document": {"type": "string", "enum": ["resume", "cover_letter", "transcript"]}}),
    _tool("press_add", "Press an Add button to open a new entry in a repeating section.",
          {"button_id": {"type": "string", "description": "id from add_buttons"}}),
    _tool("read_page", "Read the page again (after adding entries or when unsure what changed).", {}),
    _tool("ask_ali", "Ask Ali a question on Discord and wait for his answer (saved for next time).",
          {"question": S}),
    _tool("record_legal", "List a legal or attestation question and the answer given, for Ali to confirm.",
          {"question": S, "answer": S}),
    _tool("record_unsure", "List something you're not fully sure about, for the approval card.", {"item": S}),
    _tool("save_site_note", "Remember a quirk of this application system for future applications.", {"note": S}),
    _tool("page_done", "Every question on this page is handled.", {}),
]


def compact(snapshot: dict) -> str:
    """The page as short JSON (drops empty keys to save tokens)."""
    fields = [{k: v for k, v in f.items() if v not in ("", None, [], False) or k == "value"}
              for f in snapshot["fields"]]
    return json.dumps({"step": snapshot["step"], "errors": snapshot["errors"],
                       "add_buttons": snapshot["add_buttons"], "fields": fields}, ensure_ascii=False)


class Filler:
    """One instance per application run; it collects legal and unsure items for the card."""

    def __init__(self, page, site: str, posting: dict, documents: dict, form_term: str | None, driver=wd):
        self.page, self.site, self.posting, self.documents = page, site, posting, documents
        self.driver = driver  # the site's driver module (workday, icims): same function names
        self.form_term = form_term
        self.legal: list[dict] = []
        self.unsure: list[str] = []
        self.uploaded: set[str] = set()  # documents whose upload showed up in the field
        self.attempted: set[str] = set()  # documents Claude tried to upload (found a slot for)
        self.screenshot_dir = None  # set by the run: where step screenshots go
        self.step_shots: list = []
        self.step_answers: list[dict] = []  # each filled step's questions and final values
        self.touched: dict[str, str] = {}  # field id -> how it got its value (filled, asked Ali)
        self.client = anthropic.Anthropic(api_key=config.require("ANTHROPIC_API_KEY"))
        notes = db.site_notes(db.connect(), site)
        text = "\n\n".join([
            INSTRUCTIONS,
            f"<rules_md>\n{RULES.read_text(encoding='utf-8')}\n</rules_md>",
            f"<profile_facts_md>\n{FACTS.read_text(encoding='utf-8')}\n</profile_facts_md>",
            f"<resume_yaml>\n{RESUME.read_text(encoding='utf-8')}\n</resume_yaml>",
            answers.bank_text(),
            f"<site_notes site=\"{site}\">\n" + ("\n".join(f"- {n}" for n in notes) or "none yet") + "\n</site_notes>",
        ])
        self.system = [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]
        self.label = f"{posting.get('org')}, {posting.get('title')}"

    # ----- tools -----

    def run_tool(self, name: str, args: dict) -> str:
        page = self.page
        try:
            if name == "fill_text":
                return self.driver.fill_text(page, args["field_id"], args["value"])
            if name == "select_option":
                return self.driver.select_option(page, args["field_id"], args["value"])
            if name == "select_search":
                return self.driver.select_search(page, args["field_id"], args["value"])
            if name == "choose_radio":
                return self.driver.choose_radio(page, args["field_id"], args["value"])
            if name == "set_checkbox":
                return self.driver.set_checkbox(page, args["field_id"], args["checked"], args["option"] or None)
            if name == "set_date":
                return self.driver.set_date(page, args["field_id"], args["month"], args["year"], args["day"])
            if name == "upload_file":
                path = self.documents.get(args["document"])
                if not path:
                    return f"Ali has no {args['document']} for this posting"
                self.attempted.add(args["document"])
                result = self.driver.upload(page, args["field_id"], str(path))
                if result.startswith("ok"):
                    self.uploaded.add(args["document"])
                return result
            if name == "press_add":
                return self.driver.press_add(page, args["button_id"]) + "\n" + compact(self.driver.read_page(page))
            if name == "read_page":
                return compact(self.driver.read_page(page))
            if name == "ask_ali":
                answer = answers.ask_ali(args["question"], self.label, self.posting.get("id"))
                return f"Ali answered: {answer}" if answer else "Ali didn't answer in time. Leave it empty if optional; otherwise record_unsure and move on."
            if name == "record_legal":
                key = wd.words(args["question"].split("(")[0])
                if any(wd.words(item["question"].split("(")[0]) == key for item in self.legal):
                    return "already recorded"
                self.legal.append({"question": args["question"], "answer": args["answer"]})
                return "recorded for Ali to confirm"
            if name == "record_unsure":
                self.unsure.append(args["item"])
                return "recorded"
            if name == "save_site_note":
                db.add_site_note(db.connect(), self.site, args["note"])
                return "saved"
            return f"unknown tool {name}"
        except Exception as e:  # report the failure to Claude instead of crashing the run
            return f"error: {type(e).__name__}: {str(e).splitlines()[0][:300]}"

    # ----- the loop -----

    def fill_page(self, snapshot: dict, problems: list[str] | None = None) -> None:
        """Run the tool loop until Claude calls page_done (or the turn limit)."""
        intro = {
            "posting": {k: self.posting.get(k) for k in ("id", "org", "title", "location", "materials", "description")},
            "form_term": self.form_term,
            "documents_available": sorted(self.documents),
        }
        text = (f"<context>{json.dumps(intro, ensure_ascii=False)}</context>\n\n"
                f"Page now:\n{compact(snapshot)}")
        if problems:
            text += "\n\nThe site refused to continue because of these errors; fix them:\n- " + "\n- ".join(problems)
        self.asked_this_page = False
        messages = [{"role": "user", "content": text}]
        for _ in range(MAX_TURNS):
            response = self.client.beta.messages.create(
                model=config.WRITE_MODEL,
                max_tokens=16000,
                system=self.system,
                tools=TOOLS,
                messages=messages,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            if response.stop_reason == "refusal":
                raise RuntimeError("Claude declined to fill this page")
            messages.append({"role": "assistant", "content": response.content})
            calls = [b for b in response.content if b.type == "tool_use"]
            if not calls:
                messages.append({"role": "user", "content": "Use the tools: fix what's left, or call page_done."})
                continue
            results, done = [], False
            for call in calls:
                if call.name == "page_done":
                    done = True
                    results.append({"type": "tool_result", "tool_use_id": call.id, "content": "ok"})
                    continue
                result = self.run_tool(call.name, call.input)
                if "field_id" in call.input and result.startswith("ok"):
                    self.touched[call.input["field_id"]] = (
                        "from your answer to a question" if self.asked_this_page else "filled by the agent")
                if call.name == "ask_ali":
                    self.asked_this_page = True
                print(f"    {call.name}({json.dumps(call.input, ensure_ascii=False)[:90]}) -> {result[:90]}")
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": result,
                                "is_error": result.startswith(("error", "no ", "value didn't", "selection didn't",
                                                               "choice didn't", "checkbox didn't", "date didn't"))})
            messages.append({"role": "user", "content": results})
            if done:
                return
        self.unsure.append(f"Ran out of turns on the {snapshot['step']} page; check it carefully.")
