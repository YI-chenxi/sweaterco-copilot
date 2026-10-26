# Group 2 — Conversation & Clarification

## Goal

Enable reliable multi-turn conversations, context retention, slot filling, and clarification.

## Responsibilities

- Conversation state management.
- Multi-turn parameter collection and correction.
- Clarification for incomplete or ambiguous requests.
- Task switching and session isolation.

## Planned Modules

- `memory.py`: Conversation state operations.
- `clarification.py`: Clarification decisions.
- `tests/enhancement/conversation/`: Automated conversation tests.

## Interface

Input: `UnderstandingResult` and previous `ConversationState`.

Output: Updated `ConversationState` and task decisions.

Reuse existing `find_missing_slots()` and required-slot definitions.

## Rules

Do not independently redefine shared state fields.

Do not execute business tools when essential parameters are missing.

Do not modify Streamlit session handling during independent module development.

# Person 3 — Conversation Memory Implementation Handoff

## 1. Implementation Status

Branch: `feat/conversation-memory-impl`

Base: `integration/intent-enhancement`

Shared Contract baseline: `aeee91f`

The deterministic Conversation Memory module is implemented and independently tested.

### Validation

- Memory tests: 24 passed
- Full regression tests: 142 passed
- Ruff: All checks passed for the Memory implementation and tests

## 2. Files

Implementation:

`app/agent/enhancement/conversation/memory.py`

Tests:

`tests/enhancement/conversation/test_memory.py`

Shared contracts (read-only dependency):

`app/agent/enhancement/contracts.py`

## 3. Implemented Functions

### add_task(state, decision, task_id)

Adds a business task to the conversation.

- Rejects empty or duplicate task IDs.
- Rejects unsupported slot names.
- Determines PENDING or READY using required slots.
- Returns a new ConversationState without modifying the original state.

### apply_slot_update(state, update)

Applies a structured SlotUpdate to an existing task.

- Finds the task by task_id.
- Preserves existing slots unless explicitly updated.
- Supports slot replacement and removal using None.
- Recalculates missing slots and task readiness.
- Rejects unknown task IDs and unsupported slot names.
- Prevents ordinary updates to COMPLETED and FAILED tasks.
- Does not mutate the original ConversationState.

### cancel_task(state, task_id)

Removes a specified task from the active conversation state.

- Does not modify unrelated tasks.
- Rejects unknown or empty task IDs.
- Returns a new ConversationState.

Cancellation history is not retained in V1.

### retry_task(state, task_id)

Reopens a COMPLETED or FAILED task.

- Recalculates missing slots.
- Returns READY or PENDING as appropriate.
- Rejects unknown task IDs and unfinished tasks.
- Does not execute business tools.

## 4. Integration Responsibilities

Person 1 — Understanding:

Convert natural language into structured IntentDecision and task-targeted SlotUpdate information.

Person 3 — Memory:

Maintain independent task state and apply structured updates.

Person 4 — Clarification:

Validate missing or invalid parameters and request clarification when a target task cannot be identified.

Person 5 — Planning:

Execute READY tasks, preserve incomplete tasks, and avoid automatically re-executing completed tasks.

UI / Integration:

Maintain a separate ConversationState for each chat thread and pass it to the backend on each turn.

## 5. Limitations

- Memory does not call an LLM.
- Memory does not identify the target task from natural language.
- Slot value validation is limited; business validation belongs to the clarification/validation layer.
- Cancellation removes tasks without preserving an audit history.
- Retry only reopens tasks; it does not invoke business tools.
- No persistent storage or Streamlit integration is implemented.

## 6. Testing

```bash
python -m pytest -q tests/enhancement/conversation/test_memory.py
python -m pytest -q
python -m ruff check app/agent/enhancement/conversation/memory.py tests/enhancement/conversation/test_memory.py
```

## 7. Handoff Status

Conversation Memory V1 is ready for code review and integration after its PR is merged into `integration/intent-enhancement`.

This does not mean end-to-end multi-turn natural-language interaction is already available.


## Memory V1 Compatibility Patch

Branch: `fix/conversation-memory-v1`

This patch improves integration readiness without changing Shared Contract V1.0 or existing Memory function signatures.

### Changes

1. `add_task()` now stores inferred missing slots in `TaskState.decision.missing_slots`.
2. Added `mark_task_result(state, task_id, success)` for execution result tracking.

### mark_task_result()

Input:
- `state`: Current ConversationState
- `task_id`: Existing task identifier
- `success`: Boolean indicating whether tool execution completed successfully

Output:
- A new ConversationState

Rules:
- Only READY tasks may be marked.
- `success=True` sets COMPLETED.
- `success=False` sets FAILED.
- PENDING, COMPLETED and FAILED tasks cannot be directly marked.
- Unknown task IDs are rejected.
- The original state is not mutated.

Important: COMPLETED means tool execution succeeded, not that the business judgement was favorable. For example, a successful feasibility evaluation returning NOT_SUPPORTED is still COMPLETED.

### Integration

Person 5 may call `mark_task_result()` after executing a READY task.

The Planning layer remains responsible for executing business tools and interpreting execution errors.

### Validation

- 31 Memory tests passed.
- 149 full regression tests passed.
- Ruff checks passed for modified Python files.

### Compatibility

- Shared Contract V1.0 unchanged.
- Existing Memory functions remain backward compatible.
- AgentService and Streamlit integration remain separate responsibilities.
