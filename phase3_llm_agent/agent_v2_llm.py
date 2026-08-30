"""
Phase 3 — LLM-Powered Banking Support Agent (OpenAI / ChatGPT)
=================================================================
Same scenario as Phase 2 (AI Banking Support & Advisory Agent,
non-transactional), now answered by an LLM instead of keyword rules.

This is meant to be compared directly against phase2_baseline_agent:
same DEMO_QUERIES, same log format, same safety rules — but routed
through an LLM so you can show, side by side, how it handles the two
cases the baseline failed (paraphrasing + multi-part questions).

Setup
-----
    pip install openai

    # set your key as an environment variable — never hardcode it
    export OPENAI_API_KEY="sk-..."          (macOS/Linux)
    setx OPENAI_API_KEY "sk-..."            (Windows, new terminal after)

Run
---
    python agent_v2_llm.py
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MODEL_NAME = "gpt-4o-mini"          # swap to "gpt-4o" for higher quality if needed
LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "phase3_runs.log"
MEMORY_DIR = Path(__file__).resolve().parent.parent / "chat_memory"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
MEMORY_DIR.mkdir(parents=True, exist_ok=True)

SYSTEM_PROMPT = """\
You are an AI Banking Support and Advisory Agent for a retail bank. \
You have strong knowledge of account features, fees, debit cards, \
statements, disputes, loans, and general banking policies.

Rules you must always follow:
1. Answer clearly and professionally using only verified information from \
the provided banking data or policy documents. Never invent customer \
details, balances, limits, fees, or policies.
2. If the required information is unavailable, say exactly: \
"I don't know based on the available information," and note that the \
query is being escalated to a human representative.
3. Never execute, approve, or advise on money transfers, payments, \
withdrawals, credit-limit increases, loan approvals, or other financial \
transactions. Politely refuse these and direct the customer to the \
official banking app or a human representative.
4. Escalate ambiguous, suspicious, high-risk, account-access, or \
identity-related questions instead of guessing.
5. If a message contains multiple questions or requests, address EACH \
one separately and explicitly — do not silently drop any part.
6. Keep answers concise. Mention the relevant policy area when helpful, \
and clearly state when escalation or refusal applies.
"""

client = OpenAI()  # reads OPENAI_API_KEY from the environment


# ---------------------------------------------------------------------------
# Simple per-session memory (JSON file), same idea as the FileChatMessageHistory
# approach in the original Colab draft, but hand-rolled to avoid the extra
# langchain dependency and to control exactly what gets written to disk.
# ---------------------------------------------------------------------------
def _memory_path(session_id: str) -> Path:
    return MEMORY_DIR / f"{session_id}.json"


def load_history(session_id: str) -> list[dict]:
    path = _memory_path(session_id)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return []


def save_history(session_id: str, history: list[dict]) -> None:
    _memory_path(session_id).write_text(
        json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# PII masking — never write raw account numbers / emails to disk, in logs
# or in the saved chat memory.
# ---------------------------------------------------------------------------
def mask_pii(text: str) -> str:
    text = re.sub(r"\b\d{6,}\b", "[REDACTED_NUMBER]", text)
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[REDACTED_EMAIL]", text)
    return text


def log_interaction(session_id: str, query: str, response: str) -> None:
    timestamp = datetime.now().isoformat(timespec="seconds")
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(
            f"[{timestamp}] session={session_id}\n"
            f"  Q: {mask_pii(query)}\n"
            f"  A: {mask_pii(response)}\n"
        )


# ---------------------------------------------------------------------------
# Core ask() — sends system prompt + history + new question to the model
# ---------------------------------------------------------------------------
def ask(query: str, session_id: str = "demo-user") -> str:
    history = load_history(session_id)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": query})

    completion = client.chat.completions.create(
        model=MODEL_NAME,
        messages=messages,
        temperature=0,       # deterministic-ish answers for a support agent
    )
    answer = completion.choices[0].message.content

    # persist masked history only
    history.append({"role": "user", "content": mask_pii(query)})
    history.append({"role": "assistant", "content": mask_pii(answer)})
    save_history(session_id, history)

    log_interaction(session_id, query, answer)
    return answer


# ---------------------------------------------------------------------------
# Same demo set as Phase 2, so results are directly comparable.
# ---------------------------------------------------------------------------
DEMO_QUERIES = [
    "What's the daily ATM withdrawal limit on my debit card?",
    "Can you increase my credit limit?",
    "How do I dispute a transaction?",
    "Transfer €500 to my friend's account",
    "Is my account frozen? I can't log in.",
    "How much cash can I take out per day from a machine?",              # paraphrase test
    "I want to dispute a charge and also transfer money to my sister",   # multi-part test
]


def run_demo() -> None:
    print(f"Model: {MODEL_NAME}")
    print(f"Logging to: {LOG_PATH}\n")
    session_id = "demo-user"
    for query in DEMO_QUERIES:
        answer = ask(query, session_id=session_id)
        print(f"Q: {query}\nA: {answer}\n")


if __name__ == "__main__":
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit(
            "OPENAI_API_KEY is not set. Run:\n"
            '  export OPENAI_API_KEY="sk-..."   (macOS/Linux)\n'
            '  setx OPENAI_API_KEY "sk-..."     (Windows)\n'
            "then re-run this script."
        )

    run_demo()

    print("--- Interactive mode (type 'exit' to quit) ---")
    while True:
        user_input = input("Question: ").strip()
        if user_input.lower() in {"exit", "quit"}:
            break
        if not user_input:
            continue
        print(f"A: {ask(user_input)}\n")