# SweaterCo — Natural Language Enhancement Interface Contract

**Status:** Draft v0.1  
**Base branch:** `feat/b-agent-orchestration`  
**Base commit:** `0aff410`

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

Responsibilities:

- Intent classification
- Slot extraction
- Natural language variations
- Ambiguity identification

Must not execute business tools or calculate factory metrics.

## 4. Group 2 — Conversation

**Input:** `UnderstandingResult` and previous `ConversationState`.

**Proposed output:** Updated conversation state and executable or incomplete task decisions.

`ConversationState` should contain:

- `pending_tasks`
- `collected_slots`
- `missing_slots`
- `language`

Responsibilities:

- Multi-turn slot filling
- Context retention
- Parameter corrections
- Task switching
- Clarification

Conversation state must be isolated per user session.

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

- Exact Python dataclass definitions
- Multi-intent parsing output schema
- Task-specific slot storage
- Clarification result format
- Task interruption and switching behavior
- Partial execution policy
- Integration ownership

No group should independently finalize these shared structures before agreement.