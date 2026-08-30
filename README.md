# AI Banking Support & Advisory Agent
### Industry Capstone — Non-Transactional Banking Support (Track A: LangChain)

## What this is

A banking support & advisory agent that answers customer questions using
verified mock account data and real RBI/DICGC policy documents — and that
is deliberately **not allowed** to move money, approve anything, or give
legal advice. It's built as one agent that gets progressively more capable
across nine phases (rules → LLM → RAG → tools → memory → adaptive →
deployed), rather than nine disconnected demos.

**Persona:** a retail banking customer, non-technical, asking support/advisory
questions via chat — about account status, fees, limits, disputes, lost
cards, and general policy — never about executing a transaction.

**Hard safety constraints (enforced in code, not just prompted):**
1. Never executes or advises on money movement, transfers, or approvals.
2. Never gives legal advice.
3. Never invents account data or policy facts — only answers from tool
   results or retrieved documents; says so and escalates otherwise.
4. Escalates ambiguous or high-risk requests instead of guessing.
5. Never writes raw PII to logs.

## Architecture

The deployed agent (`phase8_deployment/agent_final.py`) is the integration
point for every earlier phase — it doesn't reimplement them, it imports
and runs them directly.

```text
                         ┌─────────────────────────┐
                         │   User (chat / API)      │
                         └────────────┬─────────────┘
                                      │ question, session_id
                                      ▼
                         ┌─────────────────────────┐
                         │  Hard safety guardrail    │  <- regex, runs BEFORE
                         │  (money movement, legal    │     any LLM/tool call —
                         │  advice, ambiguous cases)  │     can't be prompted around
                         └────────────┬─────────────┘
                         refuse/escalate │ pass
                                      ▼
              ┌───────────────────────────────────────────┐
              │  Phase 6 memory                             │
              │  - short-term: sliding window, in-process   │
              │  - long-term: last_account_id, topics       │
              │    (JSON file, survives restarts)           │
              └───────────────────┬─────────────────────────┘
                                  │ + memory context
                                  ▼
              ┌───────────────────────────────────────────┐
              │  Phase 7 adaptive prompting                 │
              │  reads logs/feedback.log -> if an intent has │
              │  repeated negative feedback, switch to a     │
              │  more cautious prompt variant                │
              └───────────────────┬─────────────────────────┘
                                  │ + adaptive prompt suffix
                                  ▼
              ┌───────────────────────────────────────────┐
              │  Phase 3 LLM (ChatOpenAI) + LangChain agent │
              │  with tools:                                 │
              │   • Phase 5 MCP tools (via mcp_server.py)    │
              │       - check_faq                            │
              │       - get_account_summary (mock)           │
              │       - escalate_to_human                    │
              │   • Phase 4 RAG tool                          │
              │       - search_policy_docs (FAISS index over │
              │         RBI/DICGC policy docs, cites source) │
              └───────────────────┬─────────────────────────┘
                                  │ grounded answer / escalation
                                  ▼
              ┌───────────────────────────────────────────┐
              │  PII-scrubbed structured logging             │
              │  logs/phase8_runs.log + LangSmith trace       │
              └───────────────────┬─────────────────────────┘
                                  ▼
                         ┌─────────────────────────┐
                         │   Response to user        │
                         └─────────────────────────┘

              ┌───────────────────────────────────────────┐
              │  Phase 9 evaluation harness                  │
              │  drives the SAME agent above against a       │
              │  20-question test set (test_set.csv) and      │
              │  scores refusal / escalation / grounding      │
              └───────────────────────────────────────────┘
```

## Phase structure & notes

Each phase folder holds the code/evidence for one increment. The "why"
column below is the required per-phase rationale — pulled from what's
actually documented or logged in each phase, not written after the fact.

| Phase | Folder | What it adds | Why / key finding |
|---|---|---|---|
| 1 | `phase1_problem_framing/` | Persona, workflow, success criteria, known failure cases | Test cases were deliberately chosen to include ambiguous/adversarial questions, not just easy FAQs, so Phase 9 would have real failure modes to analyze rather than a trivial 100% pass rate. |
| 2 | `phase2_baseline_agent/` | Deterministic keyword/if-else router, no LLM | **Insufficient for real users** — demonstrated with 2 deliberate failures in the demo log: (1) a paraphrased ATM question ("how much cash can I take out per day") isn't recognized because it doesn't match the hard-coded keywords, and (2) a compound question (dispute + transfer) only ever returns one intent, silently dropping half the request. |
| 3 | `phase3_llm_agent/` | LLM integration, 3 prompt variants, comparison table | **v3 selected as default** — v1 (bare) missed paraphrases and multi-part questions; v2 added safety/escalation rules but got overly conservative on plain FAQs; v3 (safety constraints + escalation logic + retrieval-grounded instructions) handles multi-part questions and grounded answers best while keeping refusal/escalation consistent. Full trade-off table in `prompt_comparison.md`. |
| 4 | `phase4_rag/` | FAISS retrieval over real RBI/DICGC policy documents | Answers are grounded in and cite the retrieved source document instead of the model's own (potentially outdated or hallucinated) knowledge of banking policy — this is what lets Phase 3's "I don't know" ungrounded refusals become real, sourced answers. |
| 5 | `phase5_tools_mcp/` | Tool use via an MCP server (`check_faq`, `get_account_summary`, `escalate_to_human`) | Correct and incorrect tool-selection traces are both captured in `phase5_tool_trace.log` (e.g. a lookup on a non-existent mock account correctly returns `NOT_FOUND` rather than a guessed balance). A max-tool-calls-per-turn guardrail prevents loop/misuse scenarios. |
| 6 | `phase6_memory_planning/` | Short-term + long-term memory, multi-step question decomposition | Explicit retention rule: short-term memory is a sliding window that lives only for the process lifetime; long-term memory persists a small set of facts (e.g. last account ID) to disk and is only cleared on an explicit user command ("forget my account"/"reset my session") — there is no silent or automatic expiry. |
| 7 | `phase7_adaptive/` | Feedback-driven prompt-variant switching | Adaptation logic: the agent logs binary (y/n) feedback per intent to `logs/feedback.log`; once an intent accumulates repeated negative feedback, the agent switches from its normal prompt to a more conservative "adaptive" prompt for that intent going forward — this is a real behavior change, not a cosmetic one, and is shared with Phase 8's deployed agent via the same feedback log. |
| 8 | `phase8_deployment/` | Deployed agent wiring phases 3–7 together behind a FastAPI service, with LangSmith tracing and graceful failure handling | Deployment assumptions/limitations are documented explicitly in `deployment_notes.md`: mock data only, no real banking backend or auth, and the MCP client is intentionally rebuilt per request rather than shared, to avoid tying an async client to the wrong event loop across the API and evaluation-harness code paths. An intentional fault path (`FORCE_DEPLOYMENT_ERROR=1`) is caught and returns a graceful fallback message rather than crashing. |
| 9 | `phase9_evaluation/` | 20-case test set, automated scoring, root-cause analysis | **Current result: 19/20 passed (95%)**, average latency ~5.0s. The one remaining failure (an out-of-scope weather question that should have been escalated but got a generic policy response instead) has a documented root cause and fix in `evaluation_report.md`. |

Supporting docs live in `docs/` (demo script, engineering justification) and
`logs/` / `chat_memory/` hold runtime evidence (interaction logs, feedback,
persisted memory) generated by actually running the agent.

## Success criteria

The agent is evaluated against 20 test cases in `phase9_evaluation/test_set.csv`,
spanning 8 categories. Success means:

- **Grounded answers** for FAQ/account-lookup questions, with the RAG tool
  citing its source document.
- **100% refusal** on money-movement, approval, and legal-advice requests —
  including adversarial/jailbreak-phrased attempts.
- **Escalation, not guessing**, on ambiguous requests, unknown accounts, and
  PII-bait questions.
- **Zero raw PII** in `logs/phase8_runs.log`, verified by an automated scan
  in the evaluation harness.

**Current result:** 19/20 test cases passing (95%), average latency ~5.0s
(see `phase9_evaluation/eval_results.csv` and `evaluation_report.md` for the
full category breakdown and the one remaining failure's root-cause analysis).
Note: one evaluation run's PII-in-logs check reported it couldn't find the
runtime log file to scan — worth re-running the evaluation harness right
before submission so the PII-scrubbing claim is verified fresh, not assumed
from an earlier run.

## Tech stack

Python, LangChain (`create_agent`), OpenAI (`gpt-4o-mini`), Model Context
Protocol (`mcp`, `langchain-mcp-adapters`) for tool calling, FAISS for
retrieval, FastAPI for deployment, LangSmith for tracing (optional,
best-effort — the app runs without it).

## Running it

```bash
# One-off Q&A demo (LLM + RAG + MCP tools + memory + adaptive prompting)
python phase8_deployment/agent_final.py --demo

# As a web service
uvicorn phase8_deployment.agent_final:app --host 0.0.0.0 --port 8000

# Evaluation suite
python phase9_evaluation/run_evaluation.py
```

See `.env.example` for required environment variables (`OPENAI_API_KEY`,
optional `LANGCHAIN_API_KEY` for LangSmith) and `phase8_deployment/deployment_notes.md`
for deployment-target notes and the intentional-failure test path.

## Known limitations

- The MCP client used for tool calls is rebuilt per request rather than
  kept alive across requests, to avoid tying an async client to the wrong
  event loop across the API and evaluation-harness code paths — see the
  docstring in `agent_final.py` for the tradeoff and the fix for
  sustained-load deployments.
- This is a demo system: mock account data only, no real banking backend,
  authentication, or transaction processing.
- `phase1_problem_framing/problem_framing.md` is still in draft form and
  needs a final editing pass before submission.
