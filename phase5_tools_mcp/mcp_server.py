"""
mcp_server.py — Phase 5: Tool Usage via MCP

This is a standalone MCP server. It runs as its own process and exposes
banking-support tools over the Model Context Protocol. The agent
(agent_final.py) connects to this as an MCP *client* and discovers/calls
these tools dynamically — it does not import this file directly.

Run standalone to sanity-check it:
    python mcp_server.py

Install:
    pip install "mcp[cli]"
"""

import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Wire in the real Phase 4 RAG corpus (RBI/DICGC docs + FAISS index) so this
# server exposes actual document search, not just the small mock FAQ table
# below. This is what phase8_deployment/agent_final.py depends on to answer
# real policy questions instead of a hardcoded, disconnected FAQ_DB copy.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from phase4_rag.agent_v3_rag import build_or_load_index, retrieve as rag_retrieve

_rag_index = None
_rag_metadata = None


def _get_rag_index():
    """Lazily build/load the FAISS index once per server process, so the
    (possibly slow, embedding-API-calling) index build only happens the
    first time a policy-document query actually comes in."""
    global _rag_index, _rag_metadata
    if _rag_index is None:
        _rag_index, _rag_metadata = build_or_load_index()
    return _rag_index, _rag_metadata


# ---------------------------------------------------------------------------
# Mock "banking" data — replace with a real (still-mock) DB/JSON file as you
# like. Nothing here is real customer data; that's the point of Phase 5/9 —
# the agent must never invent data beyond what these tools return.
# ---------------------------------------------------------------------------

FAQ_DB = {
    "atm_limit": "The standard daily ATM withdrawal limit is EUR 1,000 for debit cards.",
    "dispute_process": (
        "To dispute a transaction: log into online banking > Transactions > "
        "select the transaction > 'Raise a dispute'. Disputes are reviewed "
        "within 5-10 business days."
    ),
    "lost_card": (
        "To report a lost or stolen card: call the 24/7 card-blocking hotline "
        "immediately, or block the card yourself in the mobile app under "
        "Cards > Manage > Block Card."
    ),
    "fees": "Monthly account maintenance fee is EUR 4.90, waived if balance stays above EUR 2,000.",
}

MOCK_ACCOUNTS = {
    "ACC1001": {"account_type": "Savings", "balance": 4520.30, "status": "active"},
    "ACC1002": {"account_type": "Checking", "balance": 812.10, "status": "active"},
    "ACC1003": {"account_type": "Savings", "balance": 150.00, "status": "frozen"},
}

mcp = FastMCP("banking-support-tools")


@mcp.tool()
def check_faq(topic: str) -> str:
    """
    Look up a banking policy/FAQ fact by topic.

    Args:
        topic: one of 'atm_limit', 'dispute_process', 'lost_card', 'fees'
               (or free text — will attempt a loose match).

    Returns:
        The policy text, or a not-found message if nothing matches.
        NEVER fabricate an answer if the topic isn't found here — return
        the not-found message so the calling agent can escalate instead.
    """
    key = topic.strip().lower().replace(" ", "_")
    if key in FAQ_DB:
        return FAQ_DB[key]
    for k, v in FAQ_DB.items():
        if key in k or k in key:
            return v
    return "NOT_FOUND: no policy on file for this topic — escalate to a human agent."


@mcp.tool()
def search_policy_documents(query: str) -> str:
    """
    Search the real RBI/DICGC regulatory document corpus (Phase 4's FAISS
    vector store over data/*.md and data/pdfs/*.pdf) for passages relevant
    to the query.

    Use this for regulatory/policy questions the small check_faq table
    doesn't cover — e.g. deposit insurance limits, KYC document
    requirements, RBI Ombudsman complaint procedures and compensation
    limits.

    Args:
        query: the user's policy question, in natural language.

    Returns:
        Up to a few retrieved passages with their source document name
        (and page number for PDFs), separated by '---'. Returns NOT_FOUND
        if nothing in the corpus is similar enough — in that case, do not
        answer from outside knowledge; escalate instead.
    """
    index, metadata = _get_rag_index()
    results = rag_retrieve(query, index, metadata)
    if not results:
        return (
            "NOT_FOUND: no sufficiently relevant passage in the policy "
            "document corpus — escalate to a human agent rather than "
            "answering from outside knowledge."
        )
    formatted = []
    for r in results:
        label = r["source"] + (f" (page {r['page']})" if r.get("page") else "")
        formatted.append(f"[Source: {label}]\n{r['text']}")
    return "\n\n---\n\n".join(formatted)


@mcp.tool()
def get_account_summary(account_id: str) -> str:
    """
    Retrieve a NON-SENSITIVE summary for a mock account (type, balance, status).
    This simulates a read-only lookup. It must never be used to authorize
    or perform any transaction — it is informational only.

    Args:
        account_id: e.g. 'ACC1001'

    Returns:
        A short account summary string, or NOT_FOUND if the id doesn't exist.
    """
    acc = MOCK_ACCOUNTS.get(account_id.strip().upper())
    if not acc:
        return "NOT_FOUND: no account with that ID in the demo dataset."
    return (
        f"Account {account_id.upper()}: type={acc['account_type']}, "
        f"balance=EUR {acc['balance']:.2f}, status={acc['status']}"
    )


@mcp.tool()
def escalate_to_human(reason: str) -> str:
    """
    Escalate the current conversation to a human relationship manager.
    Use this for: money-movement requests, approval requests, legal advice
    requests, ambiguous/high-risk situations, or anything check_faq /
    get_account_summary could not resolve.

    Args:
        reason: short description of why escalation is needed (no PII).

    Returns:
        A scripted escalation confirmation message. No real ticketing
        system is wired up here on purpose — replace with a real
        integration only if your environment allows it safely.
    """
    # In a real system: write to a ticket queue, notify a human, etc.
    # Kept deliberately simple/mock per the assignment's safety scope.
    return (
        "This has been escalated to a human relationship manager "
        f"(reason logged: {reason}). They will follow up with you directly. "
        "I'm not able to process approvals, transfers, or legal advice myself."
    )


if __name__ == "__main__":
    # stdio transport: agent_final.py will launch this as a subprocess.
    # Switch to transport="streamable-http" + mcp.run(transport=...) if you
    # want it reachable as a standalone HTTP service instead.
    mcp.run(transport="stdio")
