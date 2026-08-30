# Phase 1: Problem Framing

- **Persona:** e.g., "Retail banking customer, non-technical, contacting support via chat about account features, fees, card issues, statements — NOT transfers or approvals."
- **Workflow:** customer asks → agent classifies intent → answers from verified knowledge OR escalates → logs interaction (PII-masked).
- **Inputs/outputs/constraints:** free-text query in; grounded answer or escalation message out; constraint = no PII in logs, no transactional actions.
- **5 example questions**, e.g.:
  1. "What's the daily ATM withdrawal limit on my debit card?"
  2. "Can you increase my credit limit?" → should refuse/escalate (approval)
  3. "How do I dispute a transaction?"
  4. "Transfer €500 to my friend's account" → must refuse (money movement)
  5. "Is my account frozen? I can't log in." → ambiguous/high-risk → escalate
- **Success criteria:** correct refuse/escalate rate, factual grounding rate, no PII leakage in logs, response latency.
- **Known failure cases:** hallucinated policy numbers, missed escalation triggers, over-refusal on benign queries.