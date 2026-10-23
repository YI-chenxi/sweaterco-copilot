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