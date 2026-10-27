# Person 5 task planning execution and result aggregation

## Scope and review status

Person 5 implements bounded planning, execution coordination, and result
aggregation for up to two explicitly requested business tasks. Business facts and
judgements still come from the existing A1/A2 tools through AgentOrchestrator.
This module does not parse natural language, validate all business values, change
shared contracts, or integrate AgentService or Streamlit.

Phase 4A verification completed on 2026-10-27 on `feat/multi-intent-planning`.
The module is ready for PR review within this scope, with the integration decisions
below explicitly outstanding. No commit, push, or PR has been made.

## Files and ownership

- `app/agent/enhancement/planning/planner.py`: planning and execution coordination.
- `app/agent/enhancement/planning/response.py`: pure result aggregation.
- `app/agent/enhancement/planning/README.md`: interfaces and handoff.
- `tests/enhancement/planning/test_planner.py`: 29 planning test cases.
- `tests/enhancement/planning/test_execution.py`: 39 execution test cases.
- `tests/enhancement/planning/test_response.py`: 49 aggregation test cases.

Shared contracts, Memory, Parser, Orchestrator, UI, and business tools are unchanged.
All six pending Git changes belong to Person 5. No environment or credential files
are part of the changes. The implementation files are new and remain untracked
until a separately approved staging/commit step.

## Function contracts

### Planning

```python
build_plan(state: ConversationState, task_ids: Sequence[str]) -> PlanningResult
```

Pass zero, one, or two distinct explicit IDs from `state.tasks`. The request order
is authoritative, not the conversation's storage order. Duplicate IDs, blank IDs,
missing targets, duplicate state IDs, and requests exceeding two tasks raise
ValueError before planning. A bare string is not a valid ID sequence.

Only READY tasks with a supported business intent, complete required slots, and
no unresolved issues are selected. PENDING tasks wait even if their slots appear
complete. COMPLETED and FAILED tasks are skipped, without automatic retry.

`PlanningResult.planned_tasks` contains ordered `PlannedTask(task_id, decision)`
entries with detached decision snapshots. `task_ids` derives the executable IDs
from these entries. `plan` returns a fresh existing-contract TaskPlan snapshot.
`notices` contains task-specific codes, messages, missing slots, and issue types.

TaskPlan.requires_clarification is a broad planning blocker flag. It is not the
final AgentReply.needs_clarification: aggregation distinguishes genuine missing
information from technical or integration problems.

### Reply classification

```python
classify_reply(reply: AgentReply) -> ExecutionOutcome
```

The current existing reply builders use the following structured conventions,
checked in this order. Classification never infers success from reply prose.

| Signal | Outcome | Execution coordinator action |
| --- | --- | --- |
| `metadata.status == "failed"` | TECHNICAL_FAILURE | Mark FAILED through Memory |
| `needs_clarification` | CLARIFICATION_REQUIRED | Record; leave state unchanged |
| `metadata.status == "empty"` | NO_DATA | Record; leave state unchanged |
| No status, supported metadata intent, dictionary `structured_result` | SUCCESS | Mark COMPLETED through Memory |
| Other reply signals | UNCLASSIFIED | Record; leave state unchanged |

A valid negative judgement such as NOT_SUPPORTED, AT_RISK, or ABNORMAL is still a
successful execution. An empty findings list in a completed briefing can also be
successful. Warnings alone do not imply execution failure.

### Execution coordination

```python
execute_plan(
    state: ConversationState,
    planning: PlanningResult,
    orchestrator: AgentOrchestrator,
) -> ExecutionBatch
```

The caller supplies an existing executor. The module does not construct a client
or executor. References are validated before any handle() call. Each task's
status, intent, required slots, issues, and full decision equality are rechecked
against the current per-call state snapshot before execution. Changed decisions
require replanning; stale or no-longer-eligible tasks produce SKIPPED results.

Eligible tasks call `orchestrator.handle()` sequentially in plan order. An individual
handle() exception or invalid non-AgentReply return becomes a technical failure;
other independent tasks continue. Public exception records expose the exception
class rather than raw exception text. Memory update errors propagate rather than
being mislabeled as tool failures.

Confirmed success/failure uses the existing `mark_task_result` function. Its
returned ConversationState is retained, so later updates include earlier results.
The caller must retain `ExecutionBatch.state`; reusing the original pre-execution
state can repeat work.

ExecutionBatch also exposes:

- `results`: ordered TaskExecutionResult entries, preserving task_id, decision,
  ExecutionOutcome, full reply snapshot, message, error_type, and state_updated.
- `notices`: original planning notices plus revalidation notices.
- `unresolved_task_ids`: attempted clarification, no-data, or unclassified tasks
  whose state transition still needs an agreed integration policy.

### Result aggregation

```python
aggregate_results(
    planning: PlanningResult,
    execution: ExecutionBatch,
    language: ResponseLanguage,
    *,
    task_ids: Sequence[str],
) -> AgentReply
```

Pass the original requested IDs, including waiting and skipped tasks. Do not
substitute `planning.task_ids`, which contains only executable tasks. The existing
structures do not retain the interleaving of planned tasks and notices, so this
explicit argument is required to reproduce the original display order.

Results are presented under per-task labels in that order. Headings and fallback
questions support ResponseLanguage.ENGLISH and CHINESE. Source text, business
terminology, questions, and notices are preserved rather than translated or
rewritten. Empty execution batches label selected tasks not_executed, never
completed. Historical terminal tasks are skipped; old replies are not invented.
Invalid references, conflicting outcomes/state, and partially missing execution
records raise ValueError instead of silently dropping results.

Top-level evidence, warnings, and assumptions concatenate source lists in request
order without deduplication. Ownership is preserved in independent namespaces:

```text
metadata
  language
  task_order
  tasks[task_id]
    task_id, intent, decision, current_decision
    task_status, execution_outcome, display_status, state_updated
    reply: all original AgentReply fields including nested source metadata
    message, error_type, notices
    needs_clarification, clarification_questions
  clarification_questions: task_id, question, required
  unresolved_task_ids
```

Each task's source metadata stays inside its reply namespace, preventing collisions
between identical source keys or aggregate keys. Multiple active questions are
combined with task-ID prefixes in suggested_question and retain their individual
associations in metadata. Inactive source suggestions are retained without making
them active questions. Technical failures, no-data, unknown formats, and stale
parameter changes do not automatically request clarification. Actual missing
fields or unresolved interpretation issues can require clarification.

## Local usage example without an LLM

Run from the repository root with the existing virtual environment. This example
uses the real local CSV tools and a fixed reference date matching the data snapshot.
It does not create AgentService, call a parser, load local environment settings,
or make API calls.

```python
from datetime import date

from app.agent.enhancement.contracts import ConversationState, SlotUpdate
from app.agent.enhancement.conversation.memory import add_task, apply_slot_update
from app.agent.enhancement.planning.planner import build_plan, execute_plan
from app.agent.enhancement.planning.response import aggregate_results
from app.agent.intents import Intent, IntentDecision, ResponseLanguage
from app.agent.orchestrator import AgentOrchestrator
from app.agent.settings import AgentSettings

state = ConversationState(language=ResponseLanguage.CHINESE)
state = add_task(
    state,
    IntentDecision(Intent.ORDER_STATUS, {"order_id": "ORD-057"}, language=ResponseLanguage.CHINESE),
    "task-order",
)
state = add_task(
    state,
    IntentDecision(
        Intent.ORDER_FEASIBILITY,
        {"category": "TOPS", "pieces": 800},
        language=ResponseLanguage.CHINESE,
    ),
    "task-feasibility",
)
requested_ids = ("task-order", "task-feasibility")
executor = AgentOrchestrator(settings=AgentSettings(reference_date=date(2026, 4, 1)))
planning = build_plan(state, requested_ids)
batch = execute_plan(state, planning, executor)
reply = aggregate_results(planning, batch, ResponseLanguage.CHINESE, task_ids=requested_ids)
state = batch.state
# The order query is completed; the feasibility task waits for a delivery date.

state = apply_slot_update(
    state,
    SlotUpdate("task-feasibility", {"requested_delivery_date": "2026-04-25"}),
)
planning = build_plan(state, requested_ids)
batch = execute_plan(state, planning, executor)
reply = aggregate_results(planning, batch, ResponseLanguage.CHINESE, task_ids=requested_ids)
state = batch.state
# Only feasibility executes this turn. The completed order query is skipped.
```

Inputs and returned replies are protected with detached copies. Frozen helper
objects contain nested dictionaries which callers can still edit; treat plan and
batch snapshots as read-only. Aggregation must use the batch returned for the same
plan, before replacing its state or manually changing result records.

## Verification commands and actual results

Use the already installed environment; no dependencies were installed.

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider -r fEs
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/enhancement/planning/
.venv/bin/ruff check --no-cache app/agent/enhancement/planning/ tests/enhancement/planning/
.venv/bin/ruff format --check --no-cache app/agent/enhancement/planning/ tests/enhancement/planning/
git diff --check
```

Actual Phase 4A results on 2026-10-27:

| Check | Result |
| --- | --- |
| Full repository pytest suite | 327 passed, 0 failed, 0 skipped, 0 errors |
| Person 5 cases included in that suite | 117: 29 planning, 39 execution, 49 aggregation |
| Person 5 Ruff lint | Passed |
| Person 5 Ruff format | Passed, 6 Python files and README code examples (7 files) |
| Git whitespace checks | Passed |
| Standalone real-Orchestrator/local-CSV scenarios | 6 passed, separate from pytest count |

The full pytest run and local integration run used a temporary Python audit guard
that rejected local environment-file reads and network operations. Both reported
zero attempts. The guard was process-local and did not change repository files.
Unit tests use mocked executors and deterministic reply builders. The standalone
integration check bypassed the parser and constructed the real AgentOrchestrator
with explicit settings; wrapping handle() for call counting still ran real tools.

| Local integration scenario | Observed result |
| --- | --- |
| Production normality then ORD-057 query | Both COMPLETED, 91 evidence items, requested order retained |
| Order query plus feasibility missing date | Only order executes; feasibility PENDING; clarification requested |
| Next turn supplies date through Memory | Only feasibility executes; both tasks now COMPLETED; 400 evidence items |
| Huge request with next-day delivery | Tool returns NOT_SUPPORTED; task COMPLETED, not FAILED |
| Lookup ORD-999 | NO_DATA; READY unchanged; no automatic clarification request |
| Invalid briefing top_n followed by valid order | Briefing FAILED; order still COMPLETED |

The integration checks also asserted input immutability, task-ID order, complete
per-task source replies, evidence counts, and no duplicate execution on the second
turn. No natural-language accuracy or UI end-to-end performance is claimed.

## Limitations and integration responsibilities

- Person 3 and Person 4 must agree how clarification returns a task to PENDING
  with issues, and how a legitimate no-match lookup differs from insufficient
  assessment data. The current module intentionally leaves clarification,
  no-data, and unclassified tasks READY and lists unresolved_task_ids. The caller
  must hold these tasks for resolution instead of blindly resubmitting them.
- Person 4 owns semantic value validation and specific clarification policy.
  Required-slot completeness is checked here using find_missing_slots, but legal
  quantities, dates, typo confirmations, and ambiguous targets remain upstream.
  Aggregation's generic fallback questions are not a replacement for that module.
- The integration owner must retain original requested_ids, store the returned
  state per thread, preserve execution/reply history, and explicitly handle retry
  through Memory. Reusing old input state can execute a task again. Completed and
  failed tasks are not retried when the returned updated state is retained.
- The input is a synchronous snapshot. There is no running state, lock, revision
  token, durable deduplication, or transaction across tasks. Concurrent calls and
  process interruption require an integration policy. Stale checks detect visible
  current differences, not changes that reverted to identical values.
- Existing AgentReply success conventions are not a formal outcome field. New
  reply schemas need an agreed adapter. Unknown reply formats remain unresolved;
  intent/outcome/state contradictions are rejected during aggregation.
- Original request order is supplied explicitly; it cannot be independently
  reconstructed or verified from the current shared contracts. Non-planned task
  decisions come from batch.state; original historical snapshots need persistence.
- A later turn does not automatically reproduce a previous completed task's reply
  or evidence. The integration layer must retain history for a combined history view.
- Source language, source warnings, and source evidence are preserved. A requested
  Chinese wrapper does not translate an English tool reply. Large evidence sets
  remain complete; the UI owner must decide folding, pagination, and display limits.
- UI/API integration, persistence, LLM accuracy evaluation, deployments, commits,
  pushes, and PR creation are outside this verification step.

No blocking defect was found in the tested existing-contract workflows. These
integration boundaries remain review items rather than claims of a completed
multi-turn UI or production-safe concurrent execution.
