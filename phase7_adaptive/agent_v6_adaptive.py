"""Phase 7 adaptive banking agent.

This module simulates lightweight adaptation in a banking-support agent:
- ask the user for binary feedback after each answer (`y`/`n`)
- log only non-sensitive feedback to logs/feedback.log
- count repeated negative feedback for an intent
- switch from a normal prompt to a more conservative/adaptive prompt when
  the same intent gets repeated negative feedback

Usage:
    python phase7_adaptive/agent_v6_adaptive.py
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
FEEDBACK_LOG_PATH = LOG_DIR / "feedback.log"
PHASE_LOG_PATH = Path(__file__).resolve().parent / "phase7_feedback.log"

PROMPT_VARIANTS = {
    "baseline": (
        "You are a helpful banking support assistant. Answer clearly from the "
        "available banking knowledge and escalate when data is missing or risky."
    ),
    "adaptive": (
        "You are a cautious banking support assistant. Repeated negative "
        "feedback suggests the previous answer was weak. Prefer conservative "
        "responses, answer only from verified policy sources, and escalate "
        "early when the user asks for approvals, transfers, or ambiguous risk."
    ),
}

FAQ_DB = {
    "atm_limit": "The standard daily ATM withdrawal limit is EUR 1,000 for debit cards.",
    "dispute_process": (
        "To dispute a transaction, log into online banking > Transactions > "
        "select the transaction > Raise a dispute. Disputes are reviewed within "
        "5–10 business days."
    ),
    "lost_card": (
        "To report a lost card, call the 24/7 card-blocking hotline immediately or "
        "block the card in the app under Cards > Manage > Block Card."
    ),
    "fees": "The monthly account maintenance fee is EUR 4.90, waived when the balance remains above EUR 2,000.",
}

INTENT_PATTERNS = {
    "atm_limit": ("atm", "withdrawal", "cash limit", "daily limit"),
    "lost_card": ("lost card", "stolen card", "block card", "card blocked"),
    "dispute_process": ("dispute", "transaction issue", "chargeback"),
    "fees": ("fee", "charge", "monthly fee", "maintenance"),
    "approval_or_transfer": ("increase limit", "approve", "transfer", "send money", "payment"),
}

NEGATIVE_FEEDBACK_THRESHOLD = 2


def mask_pii(text: str) -> str:
    text = re.sub(r"\b\d{6,}\b", "[REDACTED_NUMBER]", text)
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[REDACTED_EMAIL]", text)
    return text


def detect_intent(question: str) -> str:
    lowered = question.lower()
    for intent, patterns in INTENT_PATTERNS.items():
        if any(pattern in lowered for pattern in patterns):
            return intent
    return "general"


def load_negative_feedback_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    if not FEEDBACK_LOG_PATH.exists():
        return counts

    with FEEDBACK_LOG_PATH.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("helpful") is False:
                intent = str(entry.get("intent", "general"))
                counts[intent] = counts.get(intent, 0) + 1
    return counts


def log_feedback(intent: str, helpful: bool, prompt_variant: str, question: str) -> None:
    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "intent": intent,
        "helpful": helpful,
        "prompt_variant": prompt_variant,
        "question": mask_pii(question),
    }

    for target_path in (FEEDBACK_LOG_PATH, PHASE_LOG_PATH):
        with target_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def select_prompt_variant(intent: str, question: str) -> tuple[str, str]:
    feedback_counts = load_negative_feedback_counts()
    negative_count = feedback_counts.get(intent, 0)
    if negative_count >= NEGATIVE_FEEDBACK_THRESHOLD:
        return "adaptive", (
            "Adaptive prompt activated: repeated negative feedback for this intent. "
            "Using a more conservative answer path before escalation."
        )
    return "baseline", "Using the standard prompt variant."


def answer_question(question: str, prompt_variant: str) -> str:
    intent = detect_intent(question)
    if intent == "approval_or_transfer":
        return (
            "I cannot approve transfers, payments, or credit-limit changes. "
            "Please use the official banking app or contact a human relationship manager."
        )

    if intent == "atm_limit":
        return FAQ_DB["atm_limit"]
    if intent == "dispute_process":
        return FAQ_DB["dispute_process"]
    if intent == "lost_card":
        return FAQ_DB["lost_card"]
    if intent == "fees":
        return FAQ_DB["fees"]

    if prompt_variant == "adaptive":
        return (
            "I’m using the conservative mode and should avoid guessing. "
            "Please provide a more specific banking question or contact a human agent "
            "if this request involves approvals, transfer instructions, or risk."
        )

    return (
        "I can help with general banking policy questions, account basics, fees, "
        "disputes, and card issues. Please ask a specific question about one of those topics."
    )


def prompt_for_feedback() -> str:
    raw = input("Was this answer helpful? [y/N]: ").strip().lower()
    return raw in {"y", "yes"}


def main() -> None:
    print("Banking assistant adaptive demo. Type 'q' or 'quit' to exit.\n")
    while True:
        question = input("Ask a banking question: ").strip()
        if question.lower() in {"q", "quit", "exit"}:
            print("Exiting adaptive demo.")
            break

        intent = detect_intent(question)
        prompt_variant, variant_note = select_prompt_variant(intent, question)
        response = answer_question(question, prompt_variant)
        print(f"\n[{prompt_variant.upper()} MODE] {variant_note}")
        print(response)

        helpful = prompt_for_feedback()
        log_feedback(intent, helpful, prompt_variant, question)

        if not helpful:
            negative_count = load_negative_feedback_counts().get(intent, 0)
            print(
                f"Negative feedback count for intent '{intent}': {negative_count}. "
                "If this repeats, the agent switches to the adaptive prompt."
            )


if __name__ == "__main__":
    main()
