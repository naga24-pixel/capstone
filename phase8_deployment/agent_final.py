"""Phase 8 deployment-ready banking agent.

This is a thin FastAPI wrapper AROUND the real stack built in Phases 3-6 —
it does not re-implement a second, disconnected agent. Specifically it
wires in, for real:

  Phase 3 (LLM)     -> a LangChain ChatOpenAI-backed agent instead of a
                        keyword lookup table.
  Phase 4 (RAG)     -> the FAISS-backed RBI/DICGC document search, exposed
                        as an MCP tool (search_policy_documents) that the
                        agent calls dynamically.
  Phase 5 (MCP)     -> the same mcp_server.py tool server used in Phase 5,
                        connected to on startup via MultiServerMCPClient.
  Phase 6 (Memory)  -> the same ShortTermMemory / LongTermMemory classes
                        and planner (question decomposition) from
                        agent_v5_memory.py, reused (not re-copied).
  Phase 7 (Adaptive)-> the same prompt-variant selection based on
                        accumulated negative feedback in logs/feedback.log.

On top of that, Phase 8 adds: a FastAPI HTTP surface, structured
PII-scrubbed logging, best-effort LangSmith tracing, and a graceful
fallback on unhandled errors.

Run locally:
    python phase8_deployment/agent_final.py --demo
    uvicorn phase8_deployment.agent_final:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import json
import os
import re
import sys
from contextlib import asynccontextmanager, nullcontext
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Real imports from Phases 5-7 — this is the actual "wiring". Nothing below
# re-declares FAQ_DB, detect_intent, ShortTermMemory, etc. from scratch.
# ---------------------------------------------------------------------------
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langchain_mcp_adapters.client import MultiServerMCPClient

from phase6_memory_planning.agent_v5_memory import (
    LongTermMemory,
    ShortTermMemory,
    RESET_PHRASES,
    extract_account_id,
    extract_topics,
    plan as planner_decompose,
    require_openai_key,
)
from phase7_adaptive.agent_v6_adaptive import (
    NEGATIVE_FEEDBACK_THRESHOLD,
    PROMPT_VARIANTS,
    detect_intent,
    load_negative_feedback_counts,
)

try:
    from langsmith import traceable
except Exception:  # pragma: no cover
    def traceable(*args, **kwargs):
        def decorator(func):
            return func
        if args and callable(args[0]):
            return args[0]
        return decorator

try:
    from fastapi import FastAPI
    from pydantic import BaseModel
except Exception as exc:  # pragma: no cover
    raise RuntimeError(
        "FastAPI dependencies are missing. Install with: pip install fastapi uvicorn"
    ) from exc

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
RUN_LOG_PATH = LOG_DIR / "phase8_runs.log"
FEEDBACK_LOG_PATH = LOG_DIR / "feedback.log"
LONG_TERM_STORE_PATH = Path(__file__).resolve().parent / "long_term_memory.json"
MCP_SERVER_PATH = str(PROJECT_ROOT / "phase5_tools_mcp" / "mcp_server.py")

os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
os.environ.setdefault("LANGCHAIN_PROJECT", "banking-agent-capstone")
if not os.getenv("LANGSMITH_API_KEY") and os.getenv("LANGCHAIN_API_KEY"):
    os.environ["LANGSMITH_API_KEY"] = os.getenv("LANGCHAIN_API_KEY")
if not os.getenv("LANGCHAIN_API_KEY") and os.getenv("LANGSMITH_API_KEY"):
    os.environ["LANGCHAIN_API_KEY"] = os.getenv("LANGSMITH_API_KEY")

# ---------------------------------------------------------------------------
# Unified system prompt — the merged safety/behavior rules from Phases
# 3, 4, 5 and 6 (never invent data, refuse transactions, cite sources,
# route to the right tool, escalate when unsure). This is what the LLM
# actually sees; it replaces the old hardcoded intent->string mapping.
# ---------------------------------------------------------------------------
BASE_SYSTEM_PROMPT = """\
You are an AI Banking Support and Advisory Agent for a retail bank,
grounded in Indian banking regulations (RBI / DICGC). You have four
tools: check_faq, search_policy_documents, get_account_summary,
escalate_to_human.

Rules you must always follow:
1. Never invent account details, balances, limits, fees, or policy facts.
   Only state facts that came from a tool result.
2. For quick FAQ topics (ATM limits, dispute process, lost card, fees),
   use check_faq first.
3. For any other regulatory/policy question (deposit insurance, KYC
   requirements, RBI Ombudsman procedures/compensation, and anything
   check_faq returns NOT_FOUND for), use search_policy_documents. Answer
   ONLY from the returned passages, and cite the source document name(s)
   in parentheses at the end of your answer, e.g. (Source: dicgc_guide_to_deposit_insurance.md).
4. Use get_account_summary ONLY when the user gives a specific account ID
   and wants a balance/type/status lookup. It is informational only and
   must never be used to authorize a transaction.
5. If a tool returns NOT_FOUND, or the question involves money transfers,
   payments, withdrawals, credit-limit changes, loan approvals, legal
   advice, or anything ambiguous/high-risk/identity-related, call
   escalate_to_human instead of guessing. Never retry the same tool call
   with the same arguments.
6. If a message contains multiple independent questions, address EACH one
   explicitly — do not silently drop any part.
7. Keep answers concise and professional.
"""


class AskRequest(BaseModel):
    question: str
    session_id: str = "demo-user"


class FeedbackRequest(BaseModel):
    question: str = ""
    intent: str = "unknown"
    helpful: bool = False
    prompt_variant: str = "baseline"


def mask_pii(text: str) -> str:
    text = re.sub(r"\b\d{6,}\b", "[REDACTED_NUMBER]", text)
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[REDACTED_EMAIL]", text)
    return text


def safe_log(entry: dict[str, Any], path: Path) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def extract_tool_calls(result: dict) -> list[dict]:
    calls = []
    for msg in result["messages"]:
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            for tc in tool_calls:
                calls.append({"tool": tc["name"], "args": tc["args"]})
    return calls


def create_langsmith_context(name: str):
    if not os.getenv("LANGCHAIN_API_KEY"):
        return nullcontext()
    try:
        from langsmith import trace
        return trace(name=name)
    except Exception:
        return nullcontext()


# ---------------------------------------------------------------------------
# App state: the MCP client / tool-using agent are expensive to build (they
# spawn the mcp_server.py subprocess and build/load the FAISS index on
# first policy query), so they are built ONCE at startup and reused across
# requests, instead of per-request like the Phase 5/6 demo scripts do.
# Short-term memory is likewise kept per-session for the life of the
# process (still never written to disk, per Phase 6's retention rules) so
# that follow-up HTTP requests in the same session actually see prior
# turns, which a fresh-per-request agent could never do.
# ---------------------------------------------------------------------------
class AppState:
    mcp_client: MultiServerMCPClient | None = None
    agent: Any = None
    planner_llm: ChatOpenAI | None = None
    short_term_memories: dict[str, ShortTermMemory] = {}


state = AppState()


async def build_agent_stack() -> None:
    if state.agent is not None and state.planner_llm is not None:
        return
    state.mcp_client = MultiServerMCPClient(
        {
            "banking_tools": {
                "command": "python",
                "args": [MCP_SERVER_PATH],
                "transport": "stdio",
            }
        }
    )
    tools = await state.mcp_client.get_tools()
    llm = ChatOpenAI(api_key=require_openai_key(), model="gpt-4.1-mini", temperature=0)
    state.agent = create_agent(llm, tools, system_prompt=BASE_SYSTEM_PROMPT)
    state.planner_llm = llm


async def run_agent(session_id: str, question: str) -> dict[str, Any]:
    if state.agent is None or state.planner_llm is None:
        await build_agent_stack()
    return await process_question(session_id, question)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await build_agent_stack()
    yield


app = FastAPI(title="Banking Support Agent", version="2.0.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "banking-support-agent", "agent_ready": str(state.agent is not None)}


@traceable(name="banking_support_query")
async def process_question(session_id: str, question: str) -> dict[str, Any]:
    """The real Phase 3-6 pipeline: memory -> planning -> tool-using LLM
    agent -> synthesis. This is what /ask now calls, instead of a
    hardcoded intent->string lookup."""
    if state.agent is None or state.planner_llm is None:
        await build_agent_stack()

    short_term = state.short_term_memories.setdefault(session_id, ShortTermMemory())
    long_term = LongTermMemory(path=LONG_TERM_STORE_PATH)

    # Phase 6: explicit reset handling.
    if any(phrase in question.lower() for phrase in RESET_PHRASES):
        long_term.reset_session(session_id)
        response = "Done — I've cleared what I remembered about your account for this session."
        short_term.add("user", question)
        short_term.add("assistant", response)
        return {"answer": response, "intent": "memory_reset", "tools_used": [], "planning_used": False}

    explicit_account_id = extract_account_id(question)
    if explicit_account_id:
        long_term.update_last_account_id(session_id, explicit_account_id)
        long_term.add_topics(session_id, extract_topics(question))

    lt = long_term.get_session(session_id)
    memory_context = ""
    has_implicit_ref = any(
        phrase in question.lower() for phrase in ("my account", "this account", "that account", "it", "its")
    )
    if lt["last_account_id"] and not explicit_account_id and has_implicit_ref:
        memory_context = (
            f"\n\n[Memory: the user previously referenced account "
            f"{lt['last_account_id']}. Use it if this message refers to "
            f"'my account'/'it' without giving a new ID.]"
        )

    # Phase 7: escalate to the more conservative prompt variant once an
    # intent has accumulated repeated negative feedback.
    intent = detect_intent(question)
    feedback_counts = load_negative_feedback_counts()
    prompt_variant = "adaptive" if feedback_counts.get(intent, 0) >= NEGATIVE_FEEDBACK_THRESHOLD else "baseline"
    variant_context = f"\n\n{PROMPT_VARIANTS[prompt_variant]}" if prompt_variant == "adaptive" else ""

    # Phase 6: planning/decomposition of compound questions.
    sub_questions = await planner_decompose(state.planner_llm, question)
    planning_used = len(sub_questions) > 1
    sub_answers: list[str] = []
    all_tool_calls: list[dict] = []

    for sq in sub_questions:
        messages = (
            [{"role": "system", "content": BASE_SYSTEM_PROMPT + memory_context + variant_context}]
            + short_term.as_messages()
            + [{"role": "user", "content": sq}]
        )
        result = await state.agent.ainvoke({"messages": messages})
        answer = result["messages"][-1].content
        sub_answers.append(answer)
        all_tool_calls.extend(extract_tool_calls(result))

        sq_account_id = extract_account_id(sq)
        if sq_account_id:
            long_term.update_last_account_id(session_id, sq_account_id)
        long_term.add_topics(session_id, extract_topics(sq))

    final_answer = "\n".join(f"- {a}" for a in sub_answers) if planning_used else sub_answers[0]

    short_term.add("user", question)
    short_term.add("assistant", final_answer)

    return {
        "answer": final_answer,
        "intent": intent,
        "prompt_variant": prompt_variant,
        "tools_used": sorted({c["tool"] for c in all_tool_calls}),
        "planning_used": planning_used,
        "sub_questions": sub_questions,
    }


@app.post("/ask")
async def ask(payload: AskRequest) -> dict[str, Any]:
    question = (payload.question or "").strip()
    session_id = payload.session_id or "demo-user"
    request_entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "type": "request",
        "session_id": session_id,
        "question": mask_pii(question),
    }
    safe_log(request_entry, RUN_LOG_PATH)

    try:
        with create_langsmith_context("phase8_banking_agent"):
            if os.getenv("FORCE_DEPLOYMENT_ERROR") == "1":
                raise ValueError("Forced deployment fault for LangSmith error capture.")
            result = await process_question(session_id, question)

        response_entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "type": "response",
            "session_id": session_id,
            "question": mask_pii(question),
            "intent": result.get("intent"),
            "prompt_variant": result.get("prompt_variant"),
            "tools_used": result.get("tools_used"),
            "planning_used": result.get("planning_used"),
            "response": mask_pii(result["answer"]),
        }
        safe_log(response_entry, RUN_LOG_PATH)
        return {
            "question": question,
            "answer": result["answer"],
            "intent": result.get("intent"),
            "tools_used": result.get("tools_used"),
        }
    except Exception as exc:
        error_entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "type": "error",
            "session_id": session_id,
            "question": mask_pii(question),
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
        safe_log(error_entry, RUN_LOG_PATH)

        if os.getenv("LANGCHAIN_API_KEY"):
            try:
                from langsmith import trace
                with trace(name="phase8_error_capture"):
                    pass
            except Exception:
                pass

        return {
            "question": question,
            "answer": (
                "The service hit a deployment issue while processing this request. "
                "The error was logged and the system is operating in graceful fallback mode."
            ),
            "intent": "error",
            "error": str(exc),
        }


@app.post("/feedback")
def feedback(payload: FeedbackRequest) -> dict[str, Any]:
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "type": "feedback",
        "intent": payload.intent,
        "helpful": payload.helpful,
        "prompt_variant": payload.prompt_variant,
        "question": mask_pii(payload.question),
    }
    safe_log(entry, FEEDBACK_LOG_PATH)
    return {"status": "logged", "entry": entry}


async def run_demo() -> None:
    print("Phase 8 deployment demo (wired to Phases 3-6)")
    print("Type 'q' to exit\n")
    await build_agent_stack()
    session_id = "demo-user"
    while True:
        question = input("Ask a banking question: ").strip()
        if question.lower() in {"q", "quit", "exit"}:
            print("Exiting deployment demo.")
            break
        request_entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "type": "request",
            "session_id": session_id,
            "question": mask_pii(question),
        }
        safe_log(request_entry, RUN_LOG_PATH)
        result = await process_question(session_id, question)
        print("\nAnswer:", result["answer"])
        print("Intent:", result.get("intent"), "| Tools used:", result.get("tools_used"))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        import asyncio

        asyncio.run(run_demo())
    else:
        import uvicorn

        uvicorn.run(app, host="0.0.0.0", port=8000)
