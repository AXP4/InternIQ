"""Automatic checks on a package before Ali ever sees it."""

import re

from interniq.writer.compose import Package

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
    for phrase in BANNED:
        if phrase in lowered:
            problems.append(f'The letter uses "{phrase}", which sounds generated. Rephrase plainly.')
    for when in (package.term_start, package.term_end):
        if when not in body:
            problems.append(f'The letter body never states "{when}"; the closing must give the term dates.')
    org = (posting.get("org") or "").lower()
    for company in EXAMPLE_COMPANIES:
        if company.lower() not in org and re.search(rf"\b{company}\b", body):
            problems.append(f'The letter mentions {company}, which is not this employer (copied from an example?).')
    return problems
