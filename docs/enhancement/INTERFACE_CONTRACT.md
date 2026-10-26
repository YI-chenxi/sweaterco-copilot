# SweaterCo — Natural Language Enhancement Interface Contract

**Status:** Approved v1.1

**Approved by:** Person 3 (shared-contract owner), 2026-10-27

**Base branch:** `integration/intent-enhancement`

**Base commit:** `4ed9f2d`

## 1. Purpose

Define shared interfaces for three independently developed enhancement modules:

- Group 1: Natural Language Understanding
- Group 2: Conversation & Clarification
- Group 3: Multi-intent Planning

The modules must remain compatible with the existing A1 Retrieval, A2 Judgement, AgentService, and Streamlit UI.

## 2. Existing Contracts

The following existing structures must be reused:

- `Intent`
- `IntentDecision`
- `ResponseLanguage`
- `REQUIRED_SLOTS`
- `find_missing_slots()`
- `AgentReply`
- `Evidence`

Existing business intent names and slot names must not be changed without team approval.

## 3. Group 1 — Understanding

**Input:** User message and optional conversation context.

**Proposed output: `UnderstandingResult`**

- `decisions`: One or more `IntentDecision` objects.
- `is_ambiguous`: Whether the request needs further interpretation.
- `language`: User response language.
- `issues`: Structured interpretation problems that require clarification.
- `slot_updates`: Validated parameter updates targeting existing task IDs.

`InterpretationIssue` contains:

- `issue_type`: `possible_typo`, `invalid_format`, `ambiguous_reference`, or
  `too_many_tasks`.
- `field`: Affected slot, when applicable.
- `original_value`: The user-supplied value.
- `suggested_value`: A conservative correction candidate, when available.
- `decision_index`: Index of a newly parsed decision before task IDs are assigned.
- `task_id`: Existing conversation task ID, when the issue belongs to one.

Responsibilities:

- Intent classification
- Slot extraction
- Natural language variations
- Ambiguity identification
- One- or two-task decomposition
- Safe deterministic normalization
- Existing-task update extraction

Must not execute business tools or calculate factory metrics.

## 4. Group 2 — Conversation & Clarification

**Contract Version:** V1.0
**Owner:** Person 3

### 4.1 Purpose

The Conversation module manages multiple independent business tasks within one conversation.

Each task maintains its own intent, slots, and execution status.

### 4.2 TaskStatus

The following task statuses are supported:

- `PENDING`: The task requires additional information before execution.
- `READY`: Required information is available; the task can be considered for execution.
- `COMPLETED`: The task has been executed successfully.
- `FAILED`: The task execution failed.

### 4.3 TaskState

Each business task is represented by:

- `task_id: str`
- `decision: IntentDecision`
- `status: TaskStatus`
- `issues: list[InterpretationIssue]`

Each task stores its own intent and slots through `IntentDecision`.

Task IDs must be unique within a conversation.

Unresolved issues keep a task in `PENDING` even when all required slots are
present. Updating a field clears issues attached to that field, after which
readiness is recalculated.

### 4.4 ConversationState

The conversation state contains:

- `tasks: list[TaskState]`
- `language: ResponseLanguage`

Each user conversation must maintain an independent ConversationState instance.

Task-specific slots must not be stored in one shared global dictionary.

### 4.5 Multi-task Rules

1. Multiple independent tasks may exist within one conversation.
2. Each task must maintain its own parameters.
3. Slot updates must target an identifiable task.
4. Missing information must not be invented.
5. Tasks with complete information may execute independently of incomplete tasks.
6. Incomplete tasks must report missing information clearly.
7. Completed tasks must not be automatically re-executed when another task is updated.
8. Planning and Orchestration remain responsible for business execution.

### 4.6 Responsibilities

**Person 3 — Conversation Memory**

- Maintain conversation state.
- Apply structured slot updates.
- Preserve task identity.
- Manage task status transitions.
- Prevent task information from leaking between sessions.

**Person 4 — Clarification**

- Identify missing or invalid parameters.
- Generate clarification requests.
- Avoid unsupported assumptions.
- Return clarification results without directly modifying the ConversationState.

### 4.7 Interface Ownership

Person 3 is the sole maintainer of `app/agent/enhancement/contracts.py`.

Other developers must request changes to shared data structures rather than independently modifying the file.

The exact slot-update function interface will be specified separately before Memory implementation.

### 4.8 SlotUpdate Contract

**Owner:** Person 3
**Version:** V1.0

`SlotUpdate` represents a structured parameter update targeting an existing business task.

Fields:

- `task_id: str`: Unique identifier of the target task within the conversation.
- `slots: dict[str, Any]`: Parameter values to add or replace.

Example:

```python
SlotUpdate(
    task_id="task-002",
    slots={"requested_delivery_date": "2026-04-25"},
)
```

Rules:

1. The target task must exist in the current ConversationState.
2. The update must not be applied to an unrelated task.
3. Ambiguous task references must be clarified rather than guessed.
4. Missing business parameters must not be invented.
5. Existing slots not included in the update must be preserved.
6. Updated task readiness must be recalculated after applying the update.
7. Completed tasks must not be silently modified or re-executed.
8. The Conversation module owns state updates; the Planning module owns execution.

Person 1 is responsible for interpreting user language into structured task information. Person 3 is responsible for applying validated updates. Person 4 is responsible for missing/invalid parameter clarification.

### 4.9 Understanding-to-Memory Handoff

For each new decision, the orchestrator assigns a unique task ID and calls
`add_task(state, decision, task_id, issues=...)`. Issues identified by
`decision_index` are rebound to the assigned `task_id`.

For an existing task, the Understanding module returns a `SlotUpdate`; the
Conversation module validates and applies it through `apply_slot_update()`.

Example typo-confirmation flow:

1. The user enters `ODR057`.
2. Understanding leaves `order_id` missing and returns a `possible_typo` issue
   suggesting `ORD-057`.
3. Conversation memory persists that issue under the new task ID.
4. The user confirms the candidate.
5. Understanding returns `SlotUpdate(task_id=..., slots={"order_id": "ORD-057"})`.
6. Conversation memory applies the value, clears the issue, and recalculates
   readiness.

## 5. Group 3 — Planning

**Input:** One or more validated `IntentDecision` objects.

**Proposed output: `TaskPlan`**

- `tasks`: Ordered list of business tasks.
- `requires_clarification`: Whether execution is blocked by missing information.

Responsibilities:

- Multi-intent task decomposition
- Tool routing
- Execution coordination
- Result aggregation

The initial implementation should support no more than two business tasks per request.

## 6. Compatibility Rules

1. Do not modify existing A1/A2 business calculations.
2. Do not change existing `AgentService.handle_text()` behavior during independent development.
3. Do not modify `app/ui/` or API configuration files.
4. Do not invent missing business parameters.
5. Preserve Evidence, Warnings, Assumptions, and Metadata.
6. Do not let the LLM override deterministic A2 judgements.
7. New functionality must be testable independently using mocks.

## 7. Shared File Ownership

`app/agent/enhancement/contracts.py` is a shared file.

Only the designated integration maintainer should modify it after team review.

Changes to public interfaces require approval from all affected groups.

## 8. Integration

New enhancement modules will initially remain separate from the existing AgentService.

A future integration step will connect them through a backward-compatible entry point, such as:

`handle_turn(user_text, conversation_state)`

This is a proposed interface and is not yet implemented.

## 9. Open Design Decisions

Before implementation, the team must confirm:

- Clarification result format
- Task interruption and switching behavior
- Partial execution policy
- Integration ownership

No group should independently finalize these shared structures before agreement.
