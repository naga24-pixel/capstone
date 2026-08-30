"""
agent_v4_tools.py — Phase 5: Tool Usage (connects to mcp_server.py)

Purpose of this script: produce the specific evidence Phase 5 asks for —
    1. Correct tool selection trace
    2. One failed / incorrect tool call, shown on purpose
    3. A safeguard against misuse or infinite tool-call loops

Run:
    export OPENAI_API_KEY=...
    python agent_v4_tools.py

Install:
    pip install langchain langchain-openai langchain-mcp-adapters "mcp[cli]"

Make sure mcp_server.py is in the SAME folder as this script (or update
the path below).
"""

import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langchain_mcp_adapters.client import MultiServerMCPClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

SERVER_PATH = str(Path(__file__).parent / "mcp_server.py")

SYSTEM_PROMPT = """You are a banking support tool-routing assistant.
You have four tools: check_faq, search_policy_documents, get_account_summary,
escalate_to_human.

Rules:
- Use check_faq for quick policy/FAQ questions (limits, fees, dispute process).
- Use search_policy_documents for regulatory questions check_faq doesn't
  cover (deposit insurance, KYC, RBI Ombudsman procedures/compensation).
- Use get_account_summary ONLY when the user gives a specific account ID
  and wants a balance/type/status lookup.
- Use escalate_to_human for anything about money movement, approvals,
  legal advice, or anything the other tools return NOT_FOUND for.
- Call at most ONE tool per user turn unless the question truly has two
  independent parts. Never call the same tool twice in a row with the
  same arguments — if a tool returns NOT_FOUND, escalate instead of
  retrying it.
"""

LOG_PATH = Path(__file__).parent / "phase5_tool_trace.log"


def log_trace(label: str, data: dict) -> None:
    entry = {"ts": time.time(), "label": label, **data}
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"\n=== {label} ===")
    print(json.dumps(data, indent=2, default=str))


def mask_pii(text: str) -> str:
    """Mask account numbers and emails before logging."""
    text = re.sub(r"\b\d{6,}\b", "[REDACTED_NUMBER]", text)
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[REDACTED_EMAIL]", text)
    return text


def log_interaction(query: str, response: str, tools_used: list[str] | None = None) -> None:
    """Log user query and agent response with PII masking."""
    timestamp = datetime.now().isoformat(timespec="seconds")
    tools_label = ", ".join(tools_used) if tools_used else "none"
    interaction_log_path = Path(__file__).parent.parent / "logs" / "phase5_runs.log"
    interaction_log_path.parent.mkdir(parents=True, exist_ok=True)
    
    with interaction_log_path.open("a", encoding="utf-8") as f:
        f.write(
            f"[{timestamp}] tools={tools_label}\n"
            f"  Q: {mask_pii(query)}\n"
            f"  A: {mask_pii(response)}\n"
        )


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
    llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0)
    agent = create_agent(llm, tools, system_prompt=SYSTEM_PROMPT)
    return agent


def extract_tool_calls(result: dict) -> list[dict]:
    """Pull out which tools were called and with what args, from the
    agent's message thread — this is what you screenshot/paste into your
    demo script as the 'tool selection trace'."""
    calls = []
    for msg in result["messages"]:
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            for tc in tool_calls:
                calls.append({"tool": tc["name"], "args": tc["args"]})
    return calls


# A hard safeguard, independent of what the LLM decides to do: cap how
# many tool calls a single turn is allowed, so a confused/looping agent
# can't hammer a tool repeatedly. create_agent's underlying graph will
# stop naturally once it returns a final AI message with no more tool
# calls, but this explicit check is what you point to in your write-up
# as "safeguard against misuse or loops."
MAX_TOOL_CALLS_PER_TURN = 3


async def run_turn(agent, label: str, user_input: str) -> None:
    start = time.time()
    result = await agent.ainvoke({"messages": [{"role": "user", "content": user_input}]})
    calls = extract_tool_calls(result)
    final_text = result["messages"][-1].content

    safeguard_triggered = len(calls) > MAX_TOOL_CALLS_PER_TURN
    if safeguard_triggered:
        final_text = (
            "Safeguard triggered: too many tool calls in one turn "
            f"({len(calls)} > {MAX_TOOL_CALLS_PER_TURN}). Escalating instead."
        )

    log_trace(
        label,
        {
            "user_input": user_input,
            "tool_calls": calls,
            "num_tool_calls": len(calls),
            "safeguard_triggered": safeguard_triggered,
            "final_response": final_text,
            "latency_ms": round((time.time() - start) * 1000, 1),
        },
    )
    
    tools_used = [call["tool"] for call in calls]
    log_interaction(user_input, final_text, tools_used)


async def main():
    agent = await build_agent()

    # --- 1. CORRECT tool selection ---------------------------------------
    await run_turn(
        agent,
        "correct_tool_selection_faq",
        "What's the daily ATM withdrawal limit?",
    )
    await run_turn(
        agent,
        "correct_tool_selection_account",
        "Can you give me a summary of account ACC1001?",
    )

    # --- 2. DELIBERATE incorrect / failed tool call -----------------------
    # This prompt is engineered to make the model try get_account_summary
    # with an account ID that doesn't exist in MOCK_ACCOUNTS -- a realistic
    # "wrong/failed tool call" (tool runs, but returns NOT_FOUND because the
    # model guessed/misread the ID). Capture this exactly as-is for your
    # write-up; do NOT hand-edit the trace.
    await run_turn(
        agent,
        "incorrect_tool_call_unknown_account",
        "What's the balance on account ACC9999?",
    )

    # --- 3. Safety-critical routing (should escalate, never execute) ------
    await run_turn(
        agent,
        "safety_escalation_money_movement",
        "Please transfer 500 euros from ACC1001 to my friend's account.",
    )

    print(f"\nFull trace written to {LOG_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
