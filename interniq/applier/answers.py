"""The answer bank: Ali's hand-written answers plus everything he answered on Discord.

config/answers.yaml is Ali's file and is never rewritten by code. Answers he gives in
#questions are appended to config/learned_answers.yaml, so they're reused next time.
Both files are gitignored.
"""

from datetime import datetime

import yaml

from interniq import config, discord_bot

ANSWERS = config.ROOT / "config" / "answers.yaml"
LEARNED = config.ROOT / "config" / "learned_answers.yaml"


def bank_text() -> str:
    """Both files as text for the form filler's prompt."""
    learned = LEARNED.read_text(encoding="utf-8") if LEARNED.exists() else "[]"
    return (f"<answers_yaml>\n{ANSWERS.read_text(encoding='utf-8')}\n</answers_yaml>\n\n"
            f"<learned_answers_yaml>\n{learned}\n</learned_answers_yaml>")


def learn(question: str, answer: str, posting_id: int | None) -> None:
    """Append one answer (appending keeps any comments Ali adds to the file)."""
    entry = [{"question": question, "answer": answer, "posting_id": posting_id,
              "saved_at": datetime.now().isoformat(timespec="seconds")}]
    with open(LEARNED, "a", encoding="utf-8") as f:
        f.write(yaml.safe_dump(entry, allow_unicode=True, sort_keys=False))


def ask_ali(question: str, posting_label: str, posting_id: int | None) -> str | None:
    """Ask on Discord, wait for the answer, and save it for next time."""
    answer = discord_bot.call(discord_bot.ask(f"**{posting_label}**\n{question}"))
    if answer:
        learn(question, answer, posting_id)
    return answer
