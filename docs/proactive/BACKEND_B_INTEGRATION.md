# Backend B handoff for Frontend C

This document describes the stable Python entry points for the proactive pages. Frontend C should use these methods and must not call P3/P5 mutation methods directly.

## B task completion matrix

Use this matrix to audit the six Backend B items from the team assignment. “Backend complete” means the Python service path and its direct tests are present. It does not mean Frontend C has wired the page or that cloud deployment has passed acceptance.

| ID | Assignment item | Backend status | Implementation and evidence | Remaining boundary |
| --- | --- | --- | --- | --- |
| B1 | Pending proposal and detail reads | Complete | `ActionService.get_pending_proposals()` and `get_proposal()` reuse the P4 repository. Pending excludes expired or claimed proposals. `test_pending_and_detail_are_scoped_and_hide_claimed` covers scope, expiry/claim visibility, and reconciliation fields. | C still needs to render Pending and Details pages. |
| B2 | Operation history presentation | Complete | `ProactiveApplication.list_action_history()` groups existing P4 audit events by `proposal_id`; `test_operation_history_groups_by_proposal` checks one operation with an event timeline. | C should count proposal groups, not audit events, and render the timeline. |
| B3 | Register and execute real actions | Backend path complete | `build_proactive_application()` registers A1 briefing create/update/cancel and A2 watch create/cancel adapters. P4 supplies identity authorization, expiry, claim, idempotency key, target version, result, and audit. Evidence: `tests/proactive/test_backend_b_integration.py::test_composed_real_briefing_adapter_runs_only_after_confirm`, `tests/proactive/adapters/test_briefing_p4_integration.py`, and `tests/proactive/adapters/test_watch_p4_integration.py`. | C still needs to call the shared page API and refresh pages from stored results. Watch update is unsupported. |
| B4 | Unclear execution result handling | Complete | P4 exposes `execution_state` and `requires_reconciliation`. Unknown receipts remain claimed and are never retried automatically. Covered by `test_crash_after_claim_requires_reconciliation_and_never_blindly_retries`, `test_result_storage_failure_leaves_durable_claim_and_blocks_reexecution`, and `test_lost_claim_acknowledgement_blocks_execution_and_requires_reconciliation`. | C must show “needs verification” and must not show success or retry on its own. |
| B5 | Agent routing | Backend path complete | `AgentService._handle_proactive()` checks proactive routing before the ordinary agent route. `ProactiveApplication.handle_text()` routes writes through P4, reads through domain services, and stops on missing fields, missing identity, or read failure. Evidence: `tests/test_agent_service.py::test_proactive_request_is_routed_before_existing_agent_pipeline`, the three routing/fail-closed cases in `tests/proactive/test_backend_b_integration.py`, and `tests/proactive/test_p1_p4_contract.py`. | C still needs to connect chat and pages to the same configured `AgentService` instance. |
| B6 | Unified service configuration and integration acceptance | Backend composition complete; end-to-end acceptance pending | `build_proactive_application()` shares the local SQLite path across P3/P4/P5 and returns stable page-facing APIs. `AgentService.from_env()` accepts trusted identity and authorizer providers. `test_page_facing_read_apis_return_json_safe_results` and the composed adapter test cover the service boundary. Ruff passes. | The real browser session provider, C pages, complete repository regression, cloud database, and external scheduler have not been accepted here. Do not claim deployment acceptance. |

### Overall acceptance summary

Backend B's six Python service responsibilities are implemented to the statuses above. B3, B5, and B6 still depend on Frontend C or deployment work for end-to-end acceptance. The full repository test suite was not completed, so this handoff does not claim a full regression pass.

## 1. Service access and identity

The chat and pages must share the same `ProactiveApplication` instance:

```python
agent = agent_service()  # the app's cached AgentService
proactive = agent.proactive_application
if proactive is None:
    # Show “not connected” / read-only state. Do not create a demo identity here.
    ...
```

To construct it, the host passes a request-scoped, trusted identity provider and server-side authorizer to `AgentService.from_env()`:

```python
agent = AgentService.from_env(
    proactive_identity_provider=current_verified_user_context,
    proactive_authorizer=authorize_proactive_operation,
)
```

The identity provider returns an object with `user_id` and `workspace_id` resolved from a verified server session. The current account-auth branch only verifies credentials; its handoff says it does not issue browser sessions or choose workspaces. Use the session service's `resolve_current_user(verified_token)` and a server-owned workspace membership check after that session layer is connected. Never take these identifiers from a form, URL, P1 output, or model response.

The authorizer signature is `(actor_id, workspace_id, operation, proposal_or_none) -> bool` and must return exactly `True`. It receives `propose`, `confirm`, `reject`, `pending`, `get`, or `history`. P4 additionally verifies that confirm/reject/get target the creator's proposal. Missing identity or denied access fails closed. `PROACTIVE_DEMO_MODE=true` plus `PROACTIVE_DEMO_WORKSPACE` is for a clearly labeled local demo only; do not enable it on a public deployment.

Use `PROACTIVE_DB_PATH` for the shared local database file. The default is `runtime_data/proactive.sqlite3`. Service construction creates the parent directory. Pass an explicit `orders_csv` to `build_proactive_application()` when the demo data file is not the default.

## 2. Page-facing API

All methods below return JSON-compatible dictionaries. Read failures return `ok: false` with an `error_code`; an empty list means the read succeeded and there were no records.

```python
proactive.list_briefings()
proactive.get_briefing(schedule_id)
proactive.list_watches()
proactive.get_watch(watch_id)
proactive.list_pending_actions()
proactive.get_action(proposal_id)
proactive.list_action_history()
proactive.propose_action(command_dict)
proactive.confirm_action(proposal_id)
proactive.reject_action(proposal_id)
```

- `list_briefings().data` contains `items` (including `next_run_at`) and scheduled `history` with saved results. Manual immediate briefings remain the existing chat workflow.
- `list_watches().data` contains current `items`, `alerts`, and `checks`; each check includes timestamp, observed value, outcome, and evidence. The backing runner reads the local CSV snapshot.
- `list_pending_actions().data.items` includes actionable proposals only. Expired and claimed proposals are omitted.
- `get_action(id).data.item` has parameters, `expires_at`, `execution_started_at`, `execution_state`, and `requires_reconciliation`. A claimed proposal reports `in_progress_or_unknown`; a failed receipt with unknown outcome reports `outcome_unknown`. Treat either value or `requires_reconciliation=true` as needing verification; never show success based only on a button click.
- `list_action_history().data.items` is grouped by `proposal_id`; each operation has an `events` timeline. Count operations by item count, not audit-event count.

The page and chat share those same `ActionService`, `BriefingService`, and `WatchService` objects. Chat uses `AgentService.handle_turn()`; it returns an `AgentReply` whose `metadata.proactive` contains the structured service result. The UI should display `reply.text` and use the structured metadata for status/details.

## 3. Creating and confirming an action

For page forms, build the normalized command shape expected by P4. P4 validates domain fields again and performs no business write until a proposal's exact ID is confirmed:

```python
command = {
    "command_type": "create",  # create/update/cancel
    "task_type": "briefing",  # briefing/watch
    "parameters": {"interval_days": 1, "local_time": "08:00",
                    "timezone": "Asia/Singapore", "start_date": "2026-11-08"},
    "target_id": None,
    "missing_fields": [],
    "language": "en",  # en/zh
}
result = proactive.propose_action(command)
# Present result.data.proposal and its exact id for confirmation.
result = proactive.confirm_action(proposal_id)
```

For cancel/update, use the exact task UUID as `target_id`; cancel parameters must be `{}`. Watch V1 supports create/cancel only; do not expose watch update, pause, or resume. `watch.create` requires `order_id`, `condition_type`, `check_interval_minutes`, plus `deadline_at` or `expected_status` as appropriate. The UI should disable repeated submission while a request is in flight and refresh from the read APIs after each response.

P4 keeps the claim when a business write's receipt is uncertain. The UI must show “result needs verification”, retain the proposal detail, and not automatically retry or create a replacement action. The stable idempotency key remains server-side for page reads.

## 4. Runner and deployment boundary

The one-shot runners use the same database path:

```powershell
python -m app.agent.proactive.briefing.runner --database runtime_data/proactive.sqlite3
python -m app.agent.proactive.watches.runner --database runtime_data/proactive.sqlite3 --workspace <workspace-id>
```

They are not a daemon. A deployed external trigger, shared multi-instance database, browser-cookie adapter, and live CSV replacement still require deployment/integration work. Do not claim automatic cloud execution or cloud persistence until those are configured and verified.

## 5. Verification performed

- `ruff check` passes on the Backend B files.
- New Backend B tests cover pending/detail reads, claimed proposal filtering, audit grouping, ordinary-message fallback, read-failure fail-closed behavior, and unauthenticated write prevention.
- P1/P4 contract tests and A1/A2 adapter integration tests pass in individual batches. A combined pytest run in this Windows environment ended before printing its summary; the repository-wide regression count is therefore not claimed.
