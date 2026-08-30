# Engineering Justification

The project uses a layered design so the agent is useful for routine banking support while remaining constrained in the places where a bank cannot safely allow an autonomous model to act. The core choices are intentionally simple, interpretable, and auditable.

## 1. Framework and implementation choices

- Python is used as the implementation language because the project is built around a clear pattern of retrieval, prompt routing, tool invocation, and evaluation.
- LangChain is used for the agent orchestration layer, especially for tool-enabled and LLM-backed workflows.
- OpenAI models are used for the conversational reasoning layer because they handle natural language variation, paraphrasing, and multi-part requests far better than a keyword-only rule engine.
- FAISS is used as the retrieval backend for verified banking FAQs and policy content, keeping the assistant grounded in authoritative project documents instead of open-ended web knowledge.
- A lightweight JSON-based memory layer stores session chat history without exposing raw PII in persistent logs.

### 1a. Why LangChain over CrewAI, Flowise/Langflow, or a framework-free build

Four options were on the table for this project, per the assignment's
Track A/Track B split:

| Option | Why it was or wasn't chosen |
|---|---|
| **LangChain (chosen)** | Gives a `create_agent` tool-calling loop, first-party MCP adapter support (`langchain-mcp-adapters`), and a message-based state model — all of which this project uses directly for the Phase 5 MCP tools, Phase 4 RAG tool, and Phase 6 memory. It is code-first, so every decision the agent makes is inspectable and unit-testable, and its behavior is fully version-controlled alongside the rest of the repo. |
| **CrewAI** | Built around role/goal/backstory abstractions for coordinating *multiple* specialized agents. This project has one bounded domain (non-transactional banking support) and four tools — there's no natural way to split the work across roles that wouldn't be artificial, so CrewAI's core abstraction would add indirection without adding capability. |
| **Flowise / Langflow** | No-code visual builders. Rejected for this project because a no-code flow can't be reviewed with a diff, can't be unit-tested the way `run_evaluation.py` tests this agent, and — per the instructor's own guidance — Flowise is deprecated; its replacement (Langflow) still shares the same "screenshots + exported JSON instead of code" evidence problem for a build this comfortable to do in Python. |
| **Framework-free (Track B)** | Would mean hand-rolling the tool-calling loop, MCP client/session handling, and conversation state management that LangChain + `langchain-mcp-adapters` already provide correctly. For a project of this scope, that's reimplementation risk (subtle bugs in message-threading or tool-loop termination) with no corresponding benefit, since nothing here needs a capability LangChain doesn't already expose. |

LangChain was the only option that let the project spend its effort on the
banking-specific logic (safety guardrails, RAG grounding, memory rules)
rather than on infrastructure that a framework already solves correctly.

### 1b. Agent topology: single-agent (ReAct-style tool use), not multi-agent

The deployed agent (`phase8_deployment/agent_final.py`) is **one agent**
built with LangChain's `create_agent`, which itself implements a ReAct-style
loop: the model reasons about the request, decides whether to call a tool
(`check_faq`, `get_account_summary`, `escalate_to_human`, or
`search_policy_docs`), observes the tool's result, and repeats until it can
respond. That reasoning loop was kept **inside one agent** rather than
split across a multi-agent crew (e.g., a supervisor routing to a separate
"FAQ agent," "account agent," and "escalation agent"). Reasons:

- **The domain is narrow and bounded.** Four tools covering one product
  scope (non-transactional retail banking support) don't need separate
  specialized agents — a single system prompt and tool set is enough to
  reason correctly across all of them, as shown by the 19/20 pass rate on
  the Phase 9 test set.
- **Safety enforcement is easier to audit in one place.** The hard
  regex-based refusal check in `agent_final.py` runs once, before any
  LLM/tool call, regardless of which "topic" the request falls under. In a
  multi-agent design, that same guardrail would need to be duplicated (or
  centralized in a supervisor) across every specialist agent — more
  surface area for one of them to be missed or drift out of sync.
- **Latency and cost.** Multi-agent handoffs mean multiple LLM calls per
  user turn (routing decision, then specialist response, sometimes a
  supervisor synthesis on top). A single agent with tool-calling answers
  most requests in one reasoning pass, which matters for a support
  chat use case where response latency is part of the user experience.
- **Debuggability.** A single agent's tool-call trace (see
  `phase5_tools_mcp/phase5_tool_trace.log`) is a linear sequence that's
  simple to read and evaluate. A multi-agent trace would require
  correlating handoffs across agents to understand a single answer.

**When multi-agent would be the better call:** if the scope grew to cover
multiple genuinely distinct business lines with deep, mutually-irrelevant
domain logic — e.g., retail banking support, wealth-management advisory,
and business-lending support, each with its own compliance rules, tone,
and tool sets — a supervisor-plus-specialists pattern would avoid bloating
one system prompt with three domains' worth of rules and would let each
specialist be evaluated independently. That's not this project's scope,
so a single ReAct-style agent is the right-sized choice, not a default
taken without considering the alternative.

## 2. Architecture rationale

The system follows a staged pipeline:

1. User input enters the agent.
2. Safety checks classify it as a supported FAQ, transaction refusal, escalation case, account lookup, or unsupported request.
3. The agent retrieves relevant policy or FAQ knowledge using the project's verified documents.
4. The final response either answers, refuses, or escalates based on risk and evidence.
5. Logs are scrubbed before storage so that account numbers, emails, and other sensitive values are masked.

This design is preferable to a single monolithic prompt because it isolates the main risk: the LLM should answer only when grounded and safe. The retrieval layer provides factual support, while the policy layer prevents the LLM from improvising financial actions or customer-specific account data.

## 3. Agent design decisions

The agent is designed around a policy-first response model:

- It supports normal informational requests such as ATM limits, disputes, and KYC process questions.
- It refuses money movement, credit-limit changes, and approval requests because those are procedural banking actions that require formal verification and approval channels.
- It escalates account-access and identity-related questions because they may involve account security, fraud, or customer verification requirements.
- It uses tool routing only for legitimate, bounded tasks such as account-summary lookup when an account ID is explicitly provided.

This makes the system operationally realistic: the model is not asked to decide whether to execute transactions, approve requests, or disclose sensitive internal customer records.

## 4. Safety and guardrail justification

Safety is treated as a first-class requirement rather than an add-on. The project includes several independent layers:

- A system prompt that explicitly bans transfers, approvals, legal advice, and invented account details.
- A tool policy that restricts tools to specific safe tasks and prohibits repeated or irrelevant tool usage.
- A structured refusal and escalation flow for high-risk cases.
- PII masking before writing to logs or chat memory.
- A requirement that ambiguous or missing evidence leads to escalation, not guesswork.

This layered strategy matters because a single prompt-level instruction can be bypassed or misapplied in edge cases; the combination of policy logic, retrieval grounding, and tool restrictions substantially reduces unsafe behavior.

## 5. Trade-offs and constraints

The project deliberately favors controlled behavior over broad autonomy:

- A simpler rule-based baseline is explainable but brittle and fails on paraphrasing and multi-part questions.
- An LLM-only approach improves conversational flexibility but needs guardrails to prevent unsafe or unsupported answers.
- A retrieval-augmented LLM balances both goals: it gains fluency while staying anchored to project documents.
- Tool usage is intentionally narrow to reduce operational scope and make the agent easier to evaluate and defend.
- A single-agent ReAct-style design trades away the specialization a multi-agent crew could offer, in exchange for a smaller safety surface, lower latency, and a simpler trace to audit — the right trade for one bounded support domain.

The result is an agent that is not "maximally general," but is much more appropriate for a regulated banking support setting where grounded answers and safe refusals are more important than being overly helpful in risky scenarios.

## 6. Why this design is suitable for the capstone

This architecture satisfies the core capstone requirement: a useful agent that can answer supported questions, refuse dangerous requests, escalate ambiguous cases, operate within a verified information boundary, and be evaluated repeatably across a structured test set. The design is also easy to demonstrate in a live walkthrough because each safety mode can be shown with a minimal, staged prompt and the corresponding agent behavior is visible in the logs and trace output.
