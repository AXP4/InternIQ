"""Elevate job board crawler (read only)."""

import re

JOBS_URL = "https://elevate.ucalgary.ca/myAccount/engineering/jobs-engineering.htm"

# Program codes Elevate appends to titles, e.g. "Rendering Engineer Intern -ENSF ENEL".
PROGRAM_CODES = re.compile(r"\s*-\s*(ALL|EN[A-Z]{2,3}(-AOS)?)(\s+\(?EN[A-Z]{2,3}(-AOS)?\)?)*\s*$")


def clean_title(title: str) -> str:
    """The employer's job title without Elevate's program codes."""
    return PROGRAM_CODES.sub("", title or "").strip()
