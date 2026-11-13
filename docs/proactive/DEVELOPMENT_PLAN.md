# SweaterCo Proactive Agent — Parallel Development Plan V1.0

Baseline: `integration/intent-enhancement` at `cbe1032` (verify latest HEAD before branching). Read `INTERFACE_CONTRACT.md` first. Goal: all five people work concurrently with mocks; integrate later without editing shared files in parallel.

## Work ownership

| Person | Branch | Owned new module | Independent deliverables |
|---|---|---|---|
| P1 | `feat/proactive-intent-parser` | `app/agent/proactive/understanding/` | `ProactiveCommand` parser; bilingual create/update/cancel/list, missing slots, ambiguous references; parser unit tests and mock examples. |
| P2 | `test/proactive-evaluation` | `tests/proactive/`, `docs/proactive/evaluation/` | Ground-truth cases and reproducible evaluator for three features, failure taxonomy, negative and safety cases, end-to-end test plan; mock services. |
| P3 | `feat/scheduled-briefing` | `app/agent/proactive/briefing/` | `BriefingService`, schedule calculation, persistence adapter, due runner, briefing history, tests. Reuse existing briefing tool. |
| P4 | `feat/confirmed-actions` | `app/agent/proactive/actions/` | `ActionService`, proposals, confirmation/rejection, authorization checks, idempotency, audit log, tests using mock executors. |
| P5 | `feat/watch-alerts` | `app/agent/proactive/watches/` and standalone `app/ui/watch_panel.py` | `WatchService`, deterministic checks, alerts, repository adapter, tests, mock-driven right-side panel function. Do not edit main Streamlit file. |

## Shared working rules

1. Everyone branches from the same integration baseline after `git fetch`; confirm commit SHA in group. Never work directly on the integration branch.
2. No person edits `app/agent/enhancement/contracts.py`, `app/agent/intents.py`, `app/agent/service.py`, `app/agent/turn_service.py`, `app/ui/streamlit_app.py`, or existing business tool logic during independent development. Raise contract change proposals instead.
3. Each person can create local module dataclasses matching the documented JSON schema. No import dependency on another person's unfinished branch; inject mocks/fakes.
4. Every PR includes README, input/output examples, tests and commands/results, known limitations, and a mapping to contract requirements.
5. P2's test suite may start with mocks; actual cross-module acceptance happens after integration. Do not claim mock results demonstrate live LLM quality.
6. External scheduler and cloud database credentials must never appear in commits or screenshots. UI must not allow unauthenticated task mutations.

## Concurrent development timeline

- Parallel phase: P1–P5 independently implement their deliverables and unit tests.
- Integration phase: integration owner merges reviewed PRs, implements one routing adapter, shared persistence configuration, authenticated scheduler entry point, and Streamlit wiring (briefing settings, watch panel, action confirmation, histories).
- Acceptance phase: P2 coordinates live end-to-end tests; owners fix their modules; verify old business workflows and test suite; deploy and record limitations.

## Final integration checklist

- [ ] Existing four workflows and chat sessions still work.
- [ ] UI schedule toggles and chat schedule commands reach the same BriefingService.
- [ ] Watches created via chat appear in right panel; cancellation works by UI and chat.
- [ ] Every mutating command is proposed and confirmed exactly once.
- [ ] Task data and audit history survive Streamlit restart.
- [ ] External scheduler runs with browser closed and handles retry/delay.
- [ ] Demonstrated watches use clearly labeled simulated data changes when necessary.
- [ ] Public viewers cannot modify shared tasks without authorization.
- [ ] Evaluation evidence, docs, video and demo are separately prepared.

## Integration ownership

The team member coordinating public docs and Streamlit deployment owns final shared-file integration. P5 owns the watch module and panel component, not the entire integration burden. All owners remain responsible for fixing their module bugs discovered during integration.
