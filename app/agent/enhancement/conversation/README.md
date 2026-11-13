# Conversation memory and clarification

`memory.py` stores task state across turns. It adds tasks, applies validated
slot updates, cancels or retries tasks, and records execution outcomes.
`clarification.py` checks missing or invalid values before business tools run.

The module uses the shared contracts in `app.agent.enhancement.contracts`.
Understanding supplies task decisions and slot updates; memory applies them;
planning executes only ready tasks. An incomplete request stays pending until
the user supplies the required details.

Run the focused tests with:

```powershell
python -m pytest -q tests/enhancement/conversation
```
