"""
agent_v5_memory.py — Phase 6: Planning, Memory & Context

Builds on agent_v4_tools.py / mcp_server.py and adds:
    1. Multi-step reasoning/planning: compound questions are decomposed
       into ordered sub-questions, each resolved via tools, then synthesized
       into one final answer.
    2. Short-term memory: a sliding window of recent conversation turns,
       kept in-process only.
    3. Long-term memory: a small set of durable facts (e.g. the last
       account ID a user referenced) persisted to disk per session, so a
       "new session" for the same user can still recall them.
    4. Explicit retention/reset rules (see MemoryStore docstring).
    5. A scripted multi-turn conversation demonstrating context carried
       across turns, including a follow-up that has NO explicit account ID
       and must be resolved from memory.

Run:
    export OPENAI_API_KEY=...
    python agent_v5_memory.py

Install:
    pip install langchain langchain-openai langchain-mcp-adapters "mcp[cli]"

mcp_server.py must be in the same folder.
"""

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langchain_mcp_adapters.client import MultiServerMCPClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def require_openai_key() -> str:
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    placeholder_values = ("your_openai_api_key", "sk-your_openai_api_key", "your_api_key")
    if not key or key.lower() in {v.lower() for v in placeholder_values}:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Copy banking-agent-capstone/.env.example to "
            "banking-agent-capstone/.env and replace the placeholder with a valid OpenAI key."
        )
    return key

LOCAL_SERVER_PATH = Path(__file__).resolve().parent / "mcp_server.py"
FALLBACK_SERVER_PATH = Path(__file__).resolve().parents[1] / "phase5_tools_mcp" / "mcp_server.py"
SERVER_PATH = str(LOCAL_SERVER_PATH if LOCAL_SERVER_PATH.exists() else FALLBACK_SERVER_PATH)
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LONG_TERM_STORE_PATH = Path(__file__).parent / "long_term_memory.json"
TRACE_LOG_PATH = LOG_DIR / "phase6_conversation_trace.log"

SYSTEM_PROMPT = """You are a banking support & advisory assistant.
Tools available: check_faq, get_account_summary, escalate_to_human.

Rules:
- Never invent account data or policy facts — only use tool results.
- Route money movement, approvals, or legal advice to escalate_to_human.
- If the user refers to "my account" or "it" without giving an ID, use
  the account ID supplied to you in the conversation context/memory
  instead of asking again, unless no such ID is available.
"""


# ---------------------------------------------------------------------------
# Memory
#
# RETENTION / RESET RULES (this is the "define memory retention and reset
# behaviour" deliverable — keep this docstring, it's part of your evidence):
#
#   SHORT-TERM (in-process only):
#     - Holds the last MAX_SHORT_TERM_TURNS user/assistant turns.
#     - Lives only for the lifetime of the running process (this script).
#     - Never written to disk. Cleared automatically once it exceeds the
#       window (sliding window, oldest turns dropped first).
#
#   LONG-TERM (persisted to long_term_memory.json, keyed by session_id):
#     - Holds a small, explicit set of durable facts per session:
#         last_account_id, topics_discussed.
#     - Survives across process restarts (simulating "new session, same
#       user") because it's read from/written to disk.
#     - Reset behaviour: a user can explicitly say "forget my account" /
#       "reset my session" -> long-term facts for that session_id are
#       wiped. There is no automatic time-based expiry in this demo；
#       add one (e.g. 30-day TTL) before using this pattern with real data.
#     - Only non-sensitive, demo-mock fields are stored here (a mock
#       account ID like ACC1001) — never store real PII in long-term
#       memory without proper access controls.
# ---------------------------------------------------------------------------

MAX_SHORT_TERM_TURNS = 8
ACCOUNT_ID_PATTERN = re.compile(r"\bACC\d{4}\b", re.IGNORECASE)
TOPIC_KEYWORDS = {
    "account_status": ("status", "open", "active", "closed", "frozen"),
    "balance": ("balance", "how much", "available funds"),
    "atm": ("atm", "withdrawal", "cash", "atm limit"),
    "lost_card": ("lost card", "stolen card", "block card", "card blocked"),
    "fees": ("fee", "charges", "monthly fee", "maintenance fee"),
}


def extract_account_id(text: str) -> Optional[str]:
    match = ACCOUNT_ID_PATTERN.search(text)
    return match.group(0).upper() if match else None


def extract_topics(text: str) -> list[str]:
    lowered = text.lower()
    hits = []
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            hits.append(topic)
    return hits


class ShortTermMemory:
    def __init__(self):
        self.turns: list[dict] = []

    def add(self, role: str, content: str) -> None:
        self.turns.append({"role": role, "content": content})
        # sliding window retention rule
        self.turns = self.turns[-MAX_SHORT_TERM_TURNS:]

    def as_messages(self) -> list[dict]:
        return list(self.turns)


class LongTermMemory:
    def __init__(self, path: Path = LONG_TERM_STORE_PATH):
        self.path = path
        self._data: dict = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text())
        return {}

    def _save(self) -> None:
        self.path.write_text(json.dumps(self._data, indent=2))

    def get_session(self, session_id: str) -> dict:
        return self._data.get(session_id, {"last_account_id": None, "topics_discussed": []})

    def update_last_account_id(self, session_id: str, account_id: str) -> None:
        session = self.get_session(session_id)
        session["last_account_id"] = account_id
        self._data[session_id] = session
        self._save()

    def add_topic(self, session_id: str, topic: str) -> None:
        session = self.get_session(session_id)
        if topic not in session["topics_discussed"]:
            session["topics_discussed"].append(topic)
        self._data[session_id] = session
        self._save()

    def add_topics(self, session_id: str, topics: list[str]) -> None:
        for topic in topics:
            self.add_topic(session_id, topic)

    def reset_session(self, session_id: str) -> None:
        """Explicit reset — e.g. triggered by 'forget my account'."""
        self._data.pop(session_id, None)
        self._save()


# ---------------------------------------------------------------------------
# Planning: decompose compound questions into ordered sub-questions.
# ---------------------------------------------------------------------------

PLANNER_PROMPT = """Decide whether the user's message contains more than one
distinct question/request that would each need a separate lookup.

If it is a SINGLE request, respond with exactly: SINGLE

If it has MULTIPLE independent parts, respond with a numbered list of the
individual sub-questions, each on its own line, e.g.:
1. What is the daily ATM withdrawal limit?
2. How do I report a lost card?

User message: {user_input}
"""


async def plan(llm: ChatOpenAI, user_input: str) -> list[str]:
    result = await llm.ainvoke(PLANNER_PROMPT.format(user_input=user_input))
    text = result.content.strip()
    if text.upper().startswith("SINGLE"):
        return [user_input]
    sub_questions = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # strip leading "1. " / "2) " etc.
        cleaned = line.lstrip("0123456789.) ").strip()
        if cleaned:
            sub_questions.append(cleaned)
    return sub_questions or [user_input]


# ---------------------------------------------------------------------------
# Trace logging
# ---------------------------------------------------------------------------

def log_trace(label: str, data: dict) -> None:
    entry = {"ts": time.time(), "label": label, **data}
    with open(TRACE_LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"\n=== {label} ===")
    print(json.dumps(data, indent=2, default=str))


# ---------------------------------------------------------------------------
# Agent assembly
# ---------------------------------------------------------------------------

async def build_agent():
    client = MultiServerMCPClient(
        {
            "banking_tools": {
                "command": "python",
                "args": [SERVER_PATH],
                "transport": "stdio",
            }
        }
    )
    tools = await client.get_tools()
    llm = ChatOpenAI(api_key=require_openai_key(), model="gpt-4.1-mini", temperature=0)
    agent = create_agent(llm, tools, system_prompt=SYSTEM_PROMPT)
    return agent, llm


RESET_PHRASES = ("forget my account", "reset my session", "forget everything")


async def handle_turn(
    agent,
    planner_llm: ChatOpenAI,
    short_term: ShortTermMemory,
    long_term: LongTermMemory,
    session_id: str,
    user_input: str,
) -> str:
    start = time.time()

    # Explicit reset command handling.
    if any(phrase in user_input.lower() for phrase in RESET_PHRASES):
        long_term.reset_session(session_id)
        response = "Done — I've cleared what I remembered about your account for this session."
        short_term.add("user", user_input)
        short_term.add("assistant", response)
        log_trace("memory_reset", {"session_id": session_id, "response": response})
        return response

    explicit_account_id = extract_account_id(user_input)
    if explicit_account_id:
        long_term.update_last_account_id(session_id, explicit_account_id)
        long_term.add_topics(session_id, extract_topics(user_input))

    # Only reuse the stored account ID when the user is clearly asking
    # about a previously referenced account, not when they explicitly
    # provide a different ID or mention a brand-new account.
    lt = long_term.get_session(session_id)
    memory_context = ""
    has_implicit_ref = any(
        phrase in user_input.lower()
        for phrase in ("my account", "this account", "that account", "it", "its")
    )
    if lt["last_account_id"] and not explicit_account_id and has_implicit_ref:
        memory_context = (
            f"\n\n[Memory: the user previously referenced account "
            f"{lt['last_account_id']}. Use it if this message refers to "
            f"'my account'/'it' without giving a new ID.]"
        )

    # Planning step.
    sub_questions = await plan(planner_llm, user_input)
    planning_used = len(sub_questions) > 1
    sub_answers = []

    for sq in sub_questions:
        messages = (
            [{"role": "system", "content": SYSTEM_PROMPT + memory_context}]
            + short_term.as_messages()
            + [{"role": "user", "content": sq}]
        )
        result = await agent.ainvoke({"messages": messages})
        answer = result["messages"][-1].content
        sub_answers.append({"sub_question": sq, "answer": answer})

        # Capture any explicit account ID from the sub-question and record
        # the topic so long-term memory stays precise and current.
        sq_account_id = extract_account_id(sq)
        if sq_account_id:
            long_term.update_last_account_id(session_id, sq_account_id)
        long_term.add_topics(session_id, extract_topics(sq))

    if planning_used:
        final_response = "\n".join(f"- {sa['answer']}" for sa in sub_answers)
    else:
        final_response = sub_answers[0]["answer"]

    short_term.add("user", user_input)
    short_term.add("assistant", final_response)

    log_trace(
        "turn",
        {
            "session_id": session_id,
            "user_input": user_input,
            "planning_used": planning_used,
            "sub_questions": sub_questions,
            "sub_answers": sub_answers,
            "long_term_memory_after": long_term.get_session(session_id),
            "final_response": final_response,
            "latency_ms": round((time.time() - start) * 1000, 1),
        },
    )
    return final_response


# ---------------------------------------------------------------------------
# Scripted multi-turn demo — this is your "demonstrate improved
# conversation quality" evidence for Phase 6.
# ---------------------------------------------------------------------------

async def main():
    agent, planner_llm = await build_agent()
    short_term = ShortTermMemory()
    long_term = LongTermMemory()
    session_id = "demo-session-1"

    conversation = [
        # Turn 1: establishes an account ID in long-term memory.
        "What's the status of account ACC1001?",
        # Turn 2: follow-up with NO account ID — must resolve from memory.
        "What about its balance?",
        # Turn 3: compound question -> triggers planning/decomposition.
        "What's the daily ATM withdrawal limit, and how do I report a lost card?",
        # Turn 4: explicit reset command.
        "Please forget my account for this session.",
        # Turn 5: same "its balance" question again, now with NO memory —
        # should behave differently (ask for the account ID / can't resolve).
        "What about its balance?",
    ]

    for i, user_input in enumerate(conversation, start=1):
        print(f"\n{'#' * 60}\nUSER TURN {i}: {user_input}\n{'#' * 60}")
        await handle_turn(agent, planner_llm, short_term, long_term, session_id, user_input)

    print(f"\nFull conversation trace written to {TRACE_LOG_PATH}")
    print(f"Long-term memory file: {LONG_TERM_STORE_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
