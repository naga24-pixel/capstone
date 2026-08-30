# Deployment Notes

## Overview
This Phase 8 implementation turns the banking support agent into a small deployment-ready API service. It is intentionally simple and non-transactional: it answers policy/FAQ questions, refuses money movement and approval tasks, and logs interactions without storing PII.

## Run locally
From the repo root:

```bash
python banking-agent-capstone/phase8_deployment/agent_final.py --demo
```

For a web service:

```bash
uvicorn banking-agent-capstone.phase8_deployment.agent_final:app --host 0.0.0.0 --port 8000
```

If the module path is awkward due to the folder name containing a hyphen, run it directly from the file location instead:

```bash
cd banking-agent-capstone/phase8_deployment
python agent_final.py --demo
```

## Deployment target
The code is structured to be deployable to a lightweight hosting target such as:
- Hugging Face Spaces
- Streamlit Community Cloud
- a small FastAPI container on a cloud VM or app platform

This project uses a plain FastAPI app because it is simple, portable, and easy for graders to run locally. It can be wrapped in a Streamlit front end later without changing the core business logic.

## Structured logging and tracing
The app logs requests, responses, and errors to:
- `logs/phase8_runs.log`
- `logs/feedback.log`

The logs are PII-scrubbed and include timestamps, intent, prompt variant, and a masked question string.

LangSmith is supported in a best-effort way:
- if `LANGCHAIN_API_KEY` is set, the code tries to open a LangSmith trace around the request
- if no LangSmith credentials exist, the app still runs normally with a local no-op trace wrapper

## Intentional failure and graceful handling
The deployment app includes a deliberate fault path for evidence:

```bash
FORCE_DEPLOYMENT_ERROR=1 python banking-agent-capstone/phase8_deployment/agent_final.py --demo
```

This triggers a handled `ValueError`, which is caught by the API and returned as a graceful fallback message rather than crashing the service. The event is still written to the log file for troubleshooting and trace review.

## Safety and limitations
- The app never executes transfers, approvals, or payment actions.
- It never fabricates an account balance or policy fact.
- It avoids writing raw PII to any log.
- It is intentionally demo-safe and not connected to a real banking backend or authentication system.
- In real deployment, this would need production auth, rate limiting, model configuration, monitoring, and secure secrets handling.
