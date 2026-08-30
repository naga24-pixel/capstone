# Evidence Capture — Screenshots & Terminal Outputs
## Banking Support & Advisory Agent Capstone

Fill this in as you go — capture each screenshot/output right after running
the command, don't try to reconstruct it later. Save image files under
`docs/evidence/` using the filenames suggested in each section, then embed
them with the markdown already written below (just confirm the paths
match). Where a phase already has a log/CSV file as evidence, a screenshot
is optional but still useful for a quick visual skim.

---

## Phase 1 — Problem Framing

No screenshot needed (this phase is a document, not a run). Just confirm
`phase1_problem_framing/problem_framing.md` is finished prose, not template
placeholders, before final submission.

---

## Phase 2 — Baseline Agent

**Capture:** terminal output of the baseline demo run, showing the 5
example questions plus the 2 deliberate failure cases.

```bash
cd phase2_baseline_agent
python agent_v1_baseline.py
```

![Phase 2 — baseline demo terminal output](evidence/phase2_terminal.png)

- [ ] Screenshot taken
- [ ] Shows both deliberate limitation cases (paraphrase miss, dropped multi-part intent)

---

## Phase 3 — LLM Integration + Prompt Comparison

**Capture:** terminal output of the prompt-variant comparison run (the one
that generated `prompt_comparison.md`).

```bash
cd phase3_llm_agent
python agent_v2_llm.py
```

![Phase 3 — prompt comparison terminal output](evidence/phase3_terminal.png)

- [ ] Screenshot taken
- [ ] `prompt_comparison.md` matches what's shown in the screenshot (no hand-edited outputs)

---

## Phase 4 — RAG

**Capture:** terminal output showing a before/after comparison — the same
question answered without retrieval (Phase 3 style) vs. with retrieval,
citing the source document.

```bash
cd phase4_rag
python agent_v3_rag.py
```

![Phase 4 — RAG retrieval terminal output](evidence/phase4_terminal.png)

- [ ] Screenshot taken
- [ ] Output visibly cites a source document (e.g. `dicgc_faqs.md`)
- [ ] Confirmed the PDFs in `data/` are actually being indexed (check `vector_store/metadata.json` includes PDF sources, not just the `.md` files)

---

## Phase 5 — Tool Usage (MCP)

**Capture:** terminal output of the tool-trace run — correct tool
selection, one deliberately incorrect/failed tool call, and the
loop/misuse safeguard triggering.

```bash
cd phase5_tools_mcp
python agent_v4_tools.py
```

![Phase 5 — MCP tool trace terminal output](evidence/phase5_terminal.png)
![Phase 5 — MCP tool trace terminal output safety escalation](evidence/phase5_terminal_2.png)

- [ ] Screenshot taken
- [ ] `phase5_tool_trace.log` referenced/attached alongside the screenshot
- [ ] Shows at least one `NOT_FOUND` / failed tool call plus the fix

---

## Phase 6 — Memory & Planning

**Capture:** terminal output of the 5-turn scripted conversation —
especially the pair of turns that ask the same question before and after
a memory reset, to show retention/reset behavior visibly changes the
answer.

```bash
cd phase6_memory_planning
python agent_v5_memory.py
```

![Phase 6 — memory conversation terminal output](evidence/phase6_terminal_1.png)
![Phase 6 — memory conversation terminal output after memory reset](evidence/phase6_terminal_2.png)

- [ ] Screenshot taken
- [ ] Shows the before/after pair around the "forget my account" reset
- [ ] `long_term_memory.json` shown or attached to confirm persistence

---

## Phase 7 — Adaptive Behaviour

**Capture:** terminal output showing a normal-prompt response, then
repeated negative feedback, then the switch to the adaptive/conservative
prompt for the same intent.

```bash
cd phase7_adaptive
python agent_v6_adaptive.py
```

![Phase 7 — adaptive behaviour terminal output](evidence/phase7_terminal_1.png)

![Phase 7 — adaptive behaviour terminal output](evidence/phase7_terminal_2.png)

- [ ] Screenshot taken
- [ ] Shows the same question answered differently before vs. after the feedback threshold is crossed
- [ ] `logs/feedback.log` attached or shown

---

## Phase 8 — Deployment

### 8a. Local demo run

```bash
cd phase8_deployment
python agent_final.py --demo
```

![Phase 8 — deployment demo terminal output](evidence/phase8_terminal_1.png)
![Phase 8 — deployment demo terminal output uvicorn server](evidence/phase8_terminal_2.png)


### 8b. Running API service

```bash
uvicorn phase8_deployment.agent_final:app --host 0.0.0.0 --port 8000
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the daily ATM withdrawal limit?", "session_id": "demo"}'
```

![Phase 8 — API request/response](evidence/phase8_api_curl.png)

### 8c. Deployed instance (Hugging Face Spaces / Streamlit Cloud / cloud VM/ Local)

![Phase 8 — live deployed app](evidence/phase8_deployed_live.png)

*Deployment URL:* `http://localhost:8080/ask`

### 8d. Intentional error → graceful handling

```bash
FORCE_DEPLOYMENT_ERROR=1 python agent_final.py --demo
```

![Phase 8 — forced error, graceful fallback message](evidence/phase8_forced_error_terminal_1.png)
![Phase 8 — forced error, graceful fallback message](evidence/phase8_forced_error_terminal_2.png)
![Phase 8 — forced error, graceful fallback message](evidence/phase8_forced_error_terminal_3.png)
![Phase 8 — forced error, graceful fallback message langsmith output](evidence/phase8_forced_error_terminal_langsmith.png)

- [ ] Local demo screenshot taken
- [ ] API curl screenshot taken
- [ ] Live deployment screenshot taken (or clearly noted if deploying to your own cloud account instead — code review is sufficient per the instructor, but a screenshot still strengthens this)
- [ ] Forced-error terminal output taken
- [ ] Corresponding LangSmith trace for the forced error captured (see LangSmith section below)

---

## Phase 9 — Evaluation

**Capture:** terminal output of the full evaluation run — the console
summary (aggregate pass rate, per-category breakdown, average latency,
PII-in-logs check result).

```bash
cd phase9_evaluation
python run_evaluation.py
```

![Phase 9 — evaluation run terminal output](evidence/phase9_terminal.png)

- [ ] Screenshot taken
- [ ] Numbers in the screenshot match what's written in `evaluation_report.md`
- [ ] PII-in-logs check shows a definitive OK/violation result (re-run if a previous attempt reported "log file not found")

---

## LangSmith Traces

Go to your LangSmith project dashboard (`banking-agent-capstone`, or
whatever you set `LANGCHAIN_PROJECT` to) for these.

### A. Normal successful run

A trace showing a full request — system prompt, tool call(s), and final
response — with visible latency.

![LangSmith — normal run trace](evidence/langsmith_normal_trace.png)

### B. Intentional error, captured

The trace corresponding to the `FORCE_DEPLOYMENT_ERROR=1` run from Phase
8d above — should show the raised exception and that it was caught rather
than crashing the service.

![LangSmith — forced error trace](evidence/langsmith_error_trace.png)

### C. (Optional) Latency/error-rate view

If your LangSmith plan surfaces a project-level chart, a screenshot of
latency or error-rate trends across your evaluation run adds useful
context to Phase 9's report.

![LangSmith — latency/error trend (optional)](evidence/langsmith_trends.png)

- [ ] Normal-run trace captured
- [ ] Forced-error trace captured
- [ ] (Optional) trend chart captured

---

## GitHub Repository Evidence

![GitHub — commit history showing phase-by-phase progression](evidence/github_commit_history.png)

- [ ] Commit history screenshot taken, showing incremental commits (not one bulk commit)
- [ ] Repo is public or shared with the grader as required

---

## Final check before zipping

- [ ] Every checkbox above is ticked
- [ ] All screenshots saved under `docs/evidence/` with the exact filenames referenced in this file, so the markdown image links actually resolve
- [ ] This file itself is included in the submission zip (e.g. as `docs/evidence_screenshots.md`)
