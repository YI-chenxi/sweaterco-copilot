# Person 4 clarification and validation

This module asks specific questions when a request is incomplete, invalid, or
ambiguous. It validates structured inputs without making business judgements.
It reuses Person 1 normalizers and the existing required-slot definitions.

## Ownership and dependencies

New Person 4 files only:

- `app/agent/enhancement/conversation/clarification.py`
- `app/agent/enhancement/conversation/clarification_adapter.py`
- `tests/enhancement/conversation/test_clarification.py`
- `tests/enhancement/conversation/test_clarification_adapter.py`
- This `CLARIFICATION_README.md`.

No existing files are replaced. In particular, shared contracts, Memory,
Understanding, Planning, AgentService, tools, and UI are unchanged.

Core requires the Approved v1.1 contracts and EnhancedIntentParser normalizers in
the supplied integration ZIP. The adapter additionally requires Person 5's
`build_plan`, `execute_plan`, and `aggregate_results` from the supplied planning
branch (reported by its owner as commit `2893482`). ZIP archives do not contain
Git history, so that commit identity cannot be independently verified locally.
The six inspected shared dependency files are identical in both supplied ZIPs.

## Decisions adopted for integration

1. Assign new tasks unique IDs and persist their NLU issues with Memory first.
   Preserve NLU issues that do not belong to a task separately in the turn/session
   context. `decision_task_ids` can bind incoming `decision_index` issues to IDs.
2. Call `validate_request` on the explicit tasks requested for this turn. It returns
   all problems, not a single arbitrarily chosen question. An issue bound to one
   active task blocks that task only; unknown/unbound/out-of-scope references and
   too many tasks block the complete current request. No task is silently chosen.
3. `prepare_state` is the separately named integration adapter. It copies state,
   persists invalid values as existing `INVALID_FORMAT` issues, and sets blocked
   tasks to PENDING. Pure clarification never writes ConversationState.
4. Task values are checked before Planning. Complete, READY, supported, issue-free
   tasks alone may execute. An independent valid task may execute while another
   waits. UNKNOWN becomes PENDING and asks for the intended supported workflow;
   identifying a different intent creates/replaces a task through integration,
   rather than pretending it is a slot update.
5. `apply_validated_update` must replace direct calls to Memory's update function
   at this integration boundary. Only legal fields reach Memory and clear their
   old issues. Invalid answers preserve previously collected values and persist
   an issue. `None` explicitly removes a field. One valid field can be accepted
   while another field remains invalid. No global or fieldless issue is implicitly
   cleared by an unrelated update or by the word "yes".
6. Typo candidates are suggestions only. Person 1 interprets a confirmation or a
   correction; this module does not parse free text or silently accept candidates.
   A denial is NOT confirmation: retain unresolved issues until a replacement or
   explicit integration resolution. A user who supplies no new usable value is
   asked again, with no invented retry counter or history stored in this module.
7. Use Person 5's aggregation unchanged, then `attach_clarification` to replace
   generic waiting questions with specific questions. Source reply dictionaries,
   evidence, warnings, assumptions, and task order remain available unchanged.
8. After aggregation, `hold_unresolved_tasks` copies the returned execution state
   and changes attempted clarification/no-data/unclassified tasks to PENDING.
   No-data is not an invalid order ID and is not a failed business judgement.
   These outcomes do not create fabricated interpretation issues. Resume only
   through an explicit validated update or an integration review. Do not run
   Person 5 aggregation against this later held-state snapshot: it validates the
   original execution snapshot. Both snapshots are retained by the adapter.
9. Always save `ValidatedExecution.state` for the next turn, not the original
   input or the earlier `ExecutionBatch.state`. Completed/failed tasks are never
   silently reopened. A negative business judgement is still COMPLETED.

## Value policy

`ValidationPolicy(reference_date, allowed_categories=...)` requires an explicit
`datetime.date`, normally the same as `AgentSettings.reference_date`. No wall
clock, hidden environment, .env, or credentials are read by the module.

The provided snapshot has categories TOPS and ACCESSORIES. They are configurable
via `allowed_categories`; this is an explicit application vocabulary, not a
claim that other categories are inherently invalid in every factory. Stages and
safe formatting/alias normalization reuse Person 1 functions.

Quantities and optional briefing counts must be positive integers. Booleans,
floats (including 800.0), fractional strings, and nonpositive numbers are rejected.
An integer string such as "800" is safely normalized. Dates must be explicit valid
numeric dates, normalized to ISO and strictly later than the business reference
date. Relative dates remain unresolved. Order-ID format is checked, not existence;
ORD-999 reaches retrieval and can legitimately return no data.

## Interfaces

```python
validate_request(
    state, task_ids, *, policy, issues=(), decision_task_ids=(),
    is_ambiguous=False, language=None,
) -> ClarificationResult

prepare_state(state, clarification) -> ConversationState

apply_validated_update(
    state, update, *, policy, language=None,
) -> tuple[ConversationState, ClarificationResult]

run_validated_tasks(
    state, task_ids, orchestrator, *, policy, issues=(),
    decision_task_ids=(), is_ambiguous=False, language=None,
) -> ValidatedExecution
```

ClarificationResult has `task_ids`, detached per-task `tasks`, `global_problems`,
`language`, `problems`, `blocks_request`, `needs_clarification`, and
`executable_task_ids`. Each problem has `task_id` (or None), `field`, `code`,
`reason`, `question` (or None for integration holds), and optional shared `issue`.
Per-task `passed` does not imply that business feasibility is positive, nor does
it reopen historical tasks. More than two selected tasks produces a split-request
question; duplicate/unknown explicit task IDs or unsupported slot names raise
ValueError as integration errors.

Global blockers are request-level, not arbitrary task issues. Persist their
original `InterpretationIssue` objects in thread-local integration context and
pass them again until the reference is explicitly resolved. Do not treat the
absence of an issue from an unrelated new parse as proof of resolution. The
result exposes all global problems even when there are zero tasks.

Dataclasses are frozen but contain detached mutable dictionaries; treat results
as read-only snapshots. `prepare_state` rejects tasks changed since validation.
The adapter is synchronous, not a concurrency lock or durable transaction.

## Two-turn example with real local tools and no model call

```python
from datetime import date
from app.agent.enhancement.contracts import ConversationState, SlotUpdate
from app.agent.enhancement.conversation.memory import add_task
from app.agent.enhancement.conversation.clarification import ValidationPolicy
from app.agent.enhancement.conversation.clarification_adapter import (
    apply_validated_update, run_validated_tasks,
)
from app.agent.intents import Intent, IntentDecision
from app.agent.orchestrator import AgentOrchestrator
from app.agent.settings import AgentSettings

policy = ValidationPolicy(date(2026, 4, 1))
executor = AgentOrchestrator(settings=AgentSettings(reference_date=policy.reference_date))
state = add_task(ConversationState(), IntentDecision(
    Intent.ORDER_STATUS, {"order_id": "ORD-057"}), "order")
state = add_task(state, IntentDecision(Intent.ORDER_FEASIBILITY,
    {"category": "TOPS", "pieces": 800}), "feasibility")

first = run_validated_tasks(state, ["order", "feasibility"], executor, policy=policy)
state = first.state
# order is completed; feasibility waits and asks for its delivery date.

state, _ = apply_validated_update(state, SlotUpdate("feasibility",
    {"requested_delivery_date": "2026-04-25"}), policy=policy)
second = run_validated_tasks(state, ["order", "feasibility"], executor, policy=policy)
state = second.state
# Only feasibility executes. Retain second.reply and second.state in thread context.
```

## Tests and limitations

```powershell
python -m unittest tests.enhancement.conversation.test_clarification tests.enhancement.conversation.test_clarification_adapter -q
python -m pytest -q
python -m ruff check app/agent/enhancement/conversation/clarification.py app/agent/enhancement/conversation/clarification_adapter.py tests/enhancement/conversation/test_clarification.py tests/enhancement/conversation/test_clarification_adapter.py
```

Actual verified results are recorded in the accompanying handoff. Unit tests use
structured/mock input; adapter tests also run the actual local CSV tools. These
are not measurements of live model accuracy. This work does not implement
`handle_turn`, UI thread storage, natural-language intent replacement, persistence,
actions, deployment, Git commits, or PR creation. The existing 48-case NLU result
is not claimed as a Person 4 or held-out evaluation result.

The integration/UI owner still needs to connect natural-language turns, preserve
original request order and histories, retain global blockers until explicitly
resolved, and store the returned state per thread. No further shared-contract
change is required for the delivered synchronous adapter.
