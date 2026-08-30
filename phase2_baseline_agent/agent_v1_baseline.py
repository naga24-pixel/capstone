"""
Phase 2 — Baseline Banking Support Agent
==========================================
Deterministic keyword/if-else router. NO LLM call in this phase — that is
intentional. The goal of Phase 2 is to build the simplest possible working
agent and use it to *demonstrate* why rule-based matching breaks down for
real users, before Phase 3 introduces an LLM.

Scenario: AI Banking Support & Advisory Agent (Non-Transactional)
Safety rules baked in from this phase onward:
  1. Never execute or advise on money movement / approvals.
  2. Never invent customer data.
  3. Escalate ambiguous or high-risk queries instead of guessing.
  4. Never write PII into logs.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "phase2_runs.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Intent rules: (intent_name, [keywords], response_template)
# Order matters — first match wins. This is part of the brittleness we want
# to demonstrate later.
# ---------------------------------------------------------------------------
INTENT_RULES = [
    (
        "atm_limit",
        ["atm", "withdrawal limit", "withdraw limit", "cash limit"],
        "Your standard daily ATM withdrawal limit is set by your card tier "
        "and account type. Please check your card's terms and conditions "
        "or contact support to confirm the exact figure for your account.",
    ),
    (
        "credit_limit_increase",
        ["increase my credit limit", "raise my credit limit", "credit limit increase"],
        "REFUSED: I can't approve or process credit limit changes. This "
        "requires a formal review — please submit a credit limit request "
        "through the banking app or speak with a representative.",
    ),
    (
        "dispute_transaction",
        ["dispute", "unauthorized transaction", "unauthorised transaction", "wrong charge"],
        "To dispute a transaction: open the transaction in your statement, "
        "select 'Report a problem', and follow the prompts. Disputes are "
        "typically resolved within 10 business days.",
    ),
    (
        "money_transfer",
        ["transfer", "send money", "move money", "wire"],
        "REFUSED: I can't initiate or advise on money transfers. Please use "
        "the official transfer feature in your banking app, which includes "
        "the required verification steps.",
    ),
    (
        "account_access",
        ["frozen", "can't log in", "cannot log in", "locked out", "account locked"],
        "ESCALATED: Account access issues can indicate a security concern. "
        "I'm escalating this to a human representative rather than guessing "
        "— they'll verify your identity and resolve it securely.",
    ),
]

FALLBACK_RESPONSE = (
    "ESCALATED: I don't have a matching rule for that request, so I can't "
    "answer confidently. Escalating to a human representative rather than "
    "guessing."
)


def _mask_pii(text: str) -> str:
    """Very light masking so logs never contain obvious PII.
    Phase 2 keeps this simple; Phase 8/9 hardens it further."""
    text = re.sub(r"\b\d{6,}\b", "[REDACTED_NUMBER]", text)          # account/card-like numbers
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[REDACTED_EMAIL]", text)
    return text


def respond(query: str) -> tuple[str, str]:
    """Match the query against intent rules.

    Returns (intent_name, response_text). Matching is a simple
    substring/keyword check — case-insensitive, no synonym handling,
    no multi-intent handling. That's the point: it's meant to be brittle.
    """
    q = query.lower()
    for intent_name, keywords, template in INTENT_RULES:
        if any(kw in q for kw in keywords):
            return intent_name, template
    return "fallback", FALLBACK_RESPONSE


def log_interaction(query: str, intent: str, response: str) -> None:
    timestamp = datetime.now().isoformat(timespec="seconds")
    safe_query = _mask_pii(query)
    safe_response = _mask_pii(response)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(
            f"[{timestamp}] intent={intent}\n"
            f"  Q: {safe_query}\n"
            f"  A: {safe_response}\n"
        )


# ---------------------------------------------------------------------------
# Demo run: covers the 5 example questions from Phase 1, plus 2 deliberate
# failure cases that expose baseline limitations.
# ---------------------------------------------------------------------------
DEMO_QUERIES = [
    # Phase 1's 5 example questions
    "What's the daily ATM withdrawal limit on my debit card?",
    "Can you increase my credit limit?",
    "How do I dispute a transaction?",
    "Transfer €500 to my friend's account",
    "Is my account frozen? I can't log in.",

    # --- Limitation 1: paraphrased question the rules don't recognise ---
    # Same intent as the ATM question above, phrased differently.
    # Expected: should match atm_limit, but keyword rules miss it.
    "How much cash can I take out per day from a machine?",

    # --- Limitation 2: multi-part question ---
    # Combines two intents in one message. Expected: should address both
    # the dispute AND the transfer refusal, but rule-matching only ever
    # returns a single intent (first match wins), silently dropping half
    # the question.
    "I want to dispute a charge and also transfer money to my sister",
]


def run_demo() -> None:
    print(f"Logging to: {LOG_PATH}\n")
    for query in DEMO_QUERIES:
        intent, response = respond(query)
        log_interaction(query, intent, response)
        print(f"Q: {query}")
        print(f"[{intent}] {response}\n")


if __name__ == "__main__":
    run_demo()

    print("--- Interactive mode (type 'exit' to quit) ---")
    while True:
        user_input = input("Question: ").strip()
        if user_input.lower() in {"exit", "quit"}:
            break
        if not user_input:
            continue
        intent, response = respond(user_input)
        log_interaction(user_input, intent, response)
        print(f"[{intent}] {response}\n")
