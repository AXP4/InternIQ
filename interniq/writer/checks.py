"""Automatic checks on a package before Ali ever sees it."""

import re

from interniq.writer.compose import Package
from interniq.writer.resume import article

DASHES = {"—": "em dash", "–": "en dash", "‒": "figure dash", "―": "dash", "-": "hyphen"}
BANNED = ["thrilled", "passionate", "leverag", "fast paced", "i am confident", "i'm confident", "in today's"]
# Companies named in the example letters; a mention of one in another company's letter means a copied line.
EXAMPLE_COMPANIES = ["Pason", "Qualcomm", "AltaGas", "AMD"]


def plain(text: str) -> str:
    return re.sub(r"</?[bi]>", "", text)


def check(package: Package, posting: dict) -> list[str]:
    """Return a list of problems (empty means it passed)."""
    body = plain(" ".join(package.paragraphs))
    lowered = body.lower()
    problems = []

    if not 4 <= len(package.paragraphs) <= 5:
        problems.append(f"The letter has {len(package.paragraphs)} paragraphs; it needs 4 or 5.")
    for char, name in DASHES.items():
        if char in body:
            sample = body[max(0, body.index(char) - 25): body.index(char) + 25]
            problems.append(f'The letter body contains a {name} ("...{sample}..."). Rewrite without any dash.')
    for match in re.finditer(r"\b(a|an) (\d+)\b", body, flags=re.IGNORECASE):
        word, number = match.group(1), match.group(2)
        if word.lower() != article(number):
            problems.append(f'Write "{article(number)} {number}", not "{match.group(0)}".')
    for phrase in BANNED:
        if phrase in lowered:
            problems.append(f'The letter uses "{phrase}", which sounds generated. Rephrase plainly.')
    for when in (package.term_start, package.term_end):
        if when not in body:
            problems.append(f'The letter body never states "{when}"; the closing must give the term dates.')
    if not ("January 2027" in body and "August 2028" in body):
        problems.append("The closing needs one short line saying Ali is flexible on any term "
                        "between January 2027 and August 2028.")
    late = re.search(r"\b(September|October|November|December) 2028\b|\b(through|until|to|into) \w* ?2029\b", body)
    if late:
        problems.append(f'The letter offers availability past August 2028 ("{late.group(0)}"). Ali returns to school then.')
    org = (posting.get("org") or "").lower()
    for company in EXAMPLE_COMPANIES:
        if company.lower() not in org and re.search(rf"\b{company}\b", body):
            problems.append(f'The letter mentions {company}, which is not this employer (copied from an example?).')
    return problems


def same_company_term(package: Package, earlier: list[dict]) -> list[str]:
    """Warn Ali when the term differs from earlier applications to the same company.

    A warning for the approval card, not a problem to fix: the term rules decide the term.
    Entries without a full term (unconfirmed ones) are skipped.
    """
    mine = (package.term_months, package.term_start, package.term_end)
    known = {(a["term_months"], a["term_start"], a["term_end"]) for a in earlier if a.get("term_months")}
    if not known or mine in known:
        return []
    listed = "; ".join(f"{m} months, {s} to {e}" for m, s, e in sorted(known))
    reason = f" Reason: {package.term_mismatch_reason}" if package.term_mismatch_reason.strip() else ""
    return [
        f"Earlier applications to this company used {listed}; this one states {mine[0]} months, "
        f"{mine[1]} to {mine[2]}.{reason}"
    ]
