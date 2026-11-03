# Enhanced chat integration

The Streamlit AI Chat now calls `AgentService.handle_turn(text, session)`.
The existing `handle_text(text)` interface remains unchanged for previous clients.

## Final answer wording

API-backed sessions make one additional structured `gpt-6-luna` call after the
deterministic business result is complete. The model receives only the public
draft answer and its language/clarification flags; it does not receive raw
Evidence rows, task IDs, planning notices, or the full metadata payload.

The editor must lead with a bold conclusion and preserve every number, date,
order ID, qualification, and requested follow-up. Local checks reject changed
facts, language changes, missing clarification questions, and internal system
terms. Any model or validation failure returns the deterministic draft unchanged.
Offline and explicitly constructed test services do not enable this extra call
unless an `AnswerPolisher` is injected.

## Flexible understanding

The enhanced parser uses one structured model call with the SweaterCo intent
catalogue, required fields, category and production-stage aliases, order-ID
rules, conversation context, current Asia/Shanghai date, and the fixed factory
snapshot date. Natural terms such as `hoodie`/`卫衣` map to `TOPS`; the returned
slots still pass through deterministic normalization and validation.

The result has two modes. `business` returns one or two task decisions, optional
slot updates, issues, and a proposed clarification question. `conversation`
returns a direct answer for greetings, supported-format questions, capability
help, light small talk, and calendar questions without creating a task or
changing task memory. Business facts are never guessed in conversation mode.

The model may decide that a follow-up is useful and word the question naturally.
Local checks remain authoritative: missing required fields, invalid values, and
order-ID typo candidates still force clarification, while a model's spurious
format warning is discarded when a local normalizer proves the value valid.

## Ownership and data flow

- `app/agent/turn_service.py` is the integration boundary. It reuses EnhancedIntentParser, Memory, validated updates, and Person 4's `run_validated_tasks()`.
- `app/ui/chat_state.py` owns thread-local state transfer and source-reply display conversion. It contains no business calculations.
- Each Streamlit thread stores a separate `ChatSession` under `agent_session`. Cached services hold no conversation state.
- ConversationState, active request order, global blockers, and the next task number move together. Visible message history retains prior source replies and evidence.
- The saved next-turn state is the final `ValidatedExecution.state`, including tasks paused for missing data. Completed tasks are not automatically rerun when another task receives parameters.

No A1/A2 business rules, shared contracts, or individual enhancement module implementations were changed.

## Behaviour

A message can create up to two tasks. A complete independent task can execute while another waits. Follow-ups pass existing task context to the enhanced parser, then apply updates through `apply_validated_update()` before validation/planning.

Unknown messages and model failures return no invented business result and preserve existing context. Invalid quantities/dates and typo candidates require clarification. A known single typo can use the existing deterministic confirmation path.

Unbound ambiguities and too-many-task problems persist across turns. The user can explicitly choose **Start a fresh request** to discard unresolved task context, then restate the request. This keeps visible message history and does not run tools. It does not infer that an unrelated new message resolved an old ambiguity.

An attempted update to a completed/failed task does not silently reopen it. The user should make a new explicit business request or start fresh. No concurrent execution, durable persistence, scheduled monitoring, or side-effecting actions are introduced.

Quick Workflows remain deterministic and independent from AI Chat task memory.

## Run and test

Use the project's normal model configuration. API credentials stay in local environment/secrets and are never included in this change.

```powershell
python -m streamlit run app/ui/streamlit_app.py
python -m pytest -q
python -m pytest -q tests/test_chat_integration.py tests/test_streamlit_chat_integration.py
```

The new tests use queued mock model outputs plus real local business tools. Streamlit AppTest exercises the actual screen with that offline service. Passing these tests is integration evidence, not a new measurement of live-model understanding accuracy.

Suggested manual checks against the supplied 2026-04-01 factory snapshot:

1. Ask to check ORD-057 and assess 800 TOPS without a delivery date. The order completes and feasibility asks for a date.
2. Reply with an explicit date such as 2026-04-25. Only feasibility executes, with assumptions and evidence preserved.
3. Ask about ODR057, then confirm the suggested ORD-057. Nothing executes before confirmation.
4. Start a new conversation and verify the earlier pending request is absent.
5. Ask for three tasks. The request stays blocked until explicitly reset and restated.

## Verification on 28 October 2026

Base snapshot: `e77012e9098b12c31e519c3e1bff6af4241e8937`.

- Full regression: **408 passed**, including 23 new integration cases and 3 real-screen Streamlit AppTest cases.
- Existing single-turn and four deterministic business workflows remain covered by the regression suite.
- Ruff lint and formatting checks passed for all six changed/new Python files.
- Git whitespace checks passed.
- Tests used mock model outputs and real local business tools. No live model requests were made. Live-model robustness and production concurrency remain separate validation work.
