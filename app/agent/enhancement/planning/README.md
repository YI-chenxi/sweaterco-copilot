# Task planning and reply aggregation

`build_plan(state, task_ids)` selects ready business tasks in the explicit
request order. It returns planned tasks and notices for requests that are
pending, incomplete, completed, failed, or unsupported. Invalid task references
raise `ValueError` rather than creating a partial plan.

`execute_plan(...)` runs selected tasks through `AgentOrchestrator` and records
their outcomes in conversation memory. `aggregate_results(...)` combines the
results and unresolved questions into one `AgentReply`, preserving task order.
Business facts and calculations remain in the deterministic retrieval and
judgement tools.

The current parser and planner accept at most two new tasks per message.

Run the focused tests with:

```powershell
python -m pytest -q tests/enhancement/planning
```
