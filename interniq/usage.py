"""What the agent spends on the Claude API, in dollars.

The API doesn't report a prepaid credit balance to a normal API key, so the agent keeps
its own tally: every response carries token counts (`usage`), priced here per model.
Ali sets his balance once from the Console (`!balance 14.32`); status then shows that
balance minus what was spent since.
"""

import json

import anthropic

from interniq import config, db

# US dollars per million tokens: input, output, cache read, cache write (5 minute).
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
}


def cost(model: str, usage: dict) -> float:
    price = next((p for name, p in PRICES.items() if model.startswith(name)), None)
    if price is None:
        return 0.0
    tokens = (usage.get("input_tokens") or 0, usage.get("output_tokens") or 0,
              usage.get("cache_read_input_tokens") or 0, usage.get("cache_creation_input_tokens") or 0)
    return sum(n * p for n, p in zip(tokens, price)) / 1_000_000


def record(model: str, usage: dict) -> None:
    conn = db.connect()
    spent = float(db.get_setting(conn, "api_spent_usd", "0") or 0) + cost(model, usage)
    db.set_setting(conn, "api_spent_usd", f"{spent:.6f}")


def _on_response(response) -> None:
    """httpx response hook: price every Messages API reply (non-streaming JSON)."""
    if "/v1/messages" not in str(response.request.url) or response.status_code != 200:
        return
    try:
        response.read()
        body = json.loads(response.content)
        if body.get("usage"):
            record(body.get("model", ""), body["usage"])
    except Exception:
        pass  # the tally must never break a real call


def client() -> anthropic.Anthropic:
    """The Claude client every part of the agent uses, with spending recorded."""
    return anthropic.Anthropic(api_key=config.require("ANTHROPIC_API_KEY"),
                               http_client=anthropic.DefaultHttpxClient(event_hooks={"response": [_on_response]}))


def set_balance(dollars: float) -> None:
    """Ali's balance from the Console, as of now; spending is counted from here."""
    conn = db.connect()
    db.set_setting(conn, "api_balance_usd", f"{dollars:.2f}")
    db.set_setting(conn, "api_spent_usd", "0")


def balance_line() -> str:
    conn = db.connect()
    spent = float(db.get_setting(conn, "api_spent_usd", "0") or 0)
    start = db.get_setting(conn, "api_balance_usd", "")
    if not start:
        return f"${spent:.2f} spent so far (type `!balance <dollars>` with your Console balance to see what's left)"
    left = float(start) - spent
    return f"about **${left:.2f} left** (${spent:.2f} spent since you set ${float(start):.2f})"
