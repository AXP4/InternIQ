"""Automatic checks on a package before Ali ever sees it."""

import re

from interniq.writer.compose import Package

DASHES = {"—": "em dash", "–": "en dash", "‒": "figure dash", "―": "dash", "-": "hyphen"}
BANNED = ["thrilled", "passionate", "leverag", "fast paced", "i am confident", "i'm confident", "in today's"]
# Companies named in the example letters; a mention of one in another company's letter means a copied line.
EXAMPLE_COMPANIES = ["Pason", "Qualcomm", "AltaGas", "AMD"]

CLOSING = re.compile(
    r"I can start in January 2027 and am available for any term through August 2028, "
    r"(am ready to relocate to [^,]+, )?and would welcome the chance to talk\."
)
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
TERM_LENGTH = re.compile(r"\b\d+\s*months?\b", flags=re.IGNORECASE)
PAST_AUGUST_2028 = re.compile(r"\b(September|October|November|December) 2028\b|\b20(29|[3-9]\d)\b")


def plain(text: str) -> str:
    return re.sub(r"</?[bi]>", "", text)


def outside_calgary(location: str) -> bool | None:
    """True or False when the location is clear, None for remote or multiple locations."""
    loc = (location or "").lower()
    if "remote" in loc or "multiple" in loc or not loc:
        return None
    return "calgary" not in loc


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

    closing = CLOSING.search(plain(package.paragraphs[-1])) if package.paragraphs else None
    away = outside_calgary(posting.get("location"))
    if not closing:
        problems.append('The last paragraph must contain the exact sentence "I can start in January 2027 and '
                        'am available for any term through August 2028, and would welcome the chance to talk." '
                        '(with "am ready to relocate to {city}, " before "and would welcome" if the job is outside Calgary).')
    elif away is True and not closing.group(1):
        problems.append('This job is outside Calgary: add "am ready to relocate to {city}, " before "and would welcome".')
    elif away is False and closing.group(1):
        problems.append("This job is in Calgary: remove the relocation clause from the closing.")
    if TERM_LENGTH.search(body):
        problems.append(f'The letter mentions a term length ("{TERM_LENGTH.search(body).group(0)}"). '
                        "State only the January 2027 through August 2028 availability.")
    if late := PAST_AUGUST_2028.search(body):
        problems.append(f'The letter mentions a date past August 2028 ("{late.group(0)}"). Ali returns to school then.')
    if TERM_LENGTH.search(package.re_line) or re.search(rf"\b({MONTHS}) 20\d\d\b", package.re_line):
        problems.append(f'The Re: line must be the job title only, with no term: "{package.re_line}".')

    org = (posting.get("org") or "").lower()
    for company in EXAMPLE_COMPANIES:
        if company.lower() not in org and re.search(rf"\b{company}\b", body):
            problems.append(f'The letter mentions {company}, which is not this employer (copied from an example?).')
    return problems
