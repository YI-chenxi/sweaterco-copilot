# SweaterCo Proactive Agent — Interface Contract V1.0

Status: Proposed baseline for five-person parallel development. Integration baseline: `cbe1032` (`integration/intent-enhancement`).

## 1. Scope and compatibility

Add (1) scheduled morning briefings, (2) standing watches and alerts, and (3) confirmed, logged actions. Preserve existing `Intent`, `IntentDecision`, `UnderstandingResult`, `ConversationState`, `TaskPlan`, `AgentService.handle_text()`, `AgentService.handle_turn()`, `ChatSession`, and the four deterministic business tools. Existing `TaskStatus` refers to conversation tasks and MUST NOT represent persisted scheduled tasks.

Do not modify shared legacy files during parallel implementation. Integrate proactive command routing at one controlled entry point after all module tests pass. A proactive request must not fall through to ordinary morning-briefing execution by mistake.

## 2. Common data and command schema

`ProactiveCommand` (P1 output, JSON-compatible):

- `command_type`: one of `create`, `update`, `cancel`, `list`, `get`, `confirm`, `reject`, `unknown`.
- `task_type`: `briefing`, `watch`, or `null` (for confirmation / unknown).
- `parameters`: dictionary; only recognized, normalized fields; never invent missing values.
- `target_id`: string or null; identifier for update/cancel/get.
- `missing_fields`: list of strings.
- `language`: `en` or `zh`.
- `reference_text`: original user request (optional for audit; do not store secrets).

`ServiceResult` (P3/P4/P5 return, JSON-compatible):

- `ok`: boolean; `status`: machine-readable string; `id`: string or null.
- `data`: dictionary; `error_code`: string or null; `message`: user-safe text.

All timestamps: timezone-aware ISO 8601 UTC in storage, Singapore time (`Asia/Singapore`) in UI. IDs: globally unique UUID strings. `workspace_id` is mandatory for persistent records; V1 may use a single explicitly labeled demo workspace, but public anonymous visitors MUST NOT be able to mutate it. Secure write access is required. Do not use `st.session_state` as persistent storage.

## 3. Briefing module (P3)

Interface (signatures are integration targets, not existing code):

```python
class BriefingService:
    def create_schedule(self, workspace_id: str, parameters: dict) -> dict: ...
    def update_schedule(self, workspace_id: str, schedule_id: str, parameters: dict) -> dict: ...
    def cancel_schedule(self, workspace_id: str, schedule_id: str) -> dict: ...
    def list_schedules(self, workspace_id: str) -> list[dict]: ...
    def list_history(self, workspace_id: str, schedule_id: str | None = None) -> list[dict]: ...
    def run_due_briefings(self, now: datetime) -> list[dict]: ...
```

Parameters: `interval_days` positive integer (UI presets 1, 3, 7); `local_time` in `HH:MM`; `timezone` IANA identifier (default `Asia/Singapore`); `start_date` ISO local date. Every N days is anchored to `start_date`, not calendar modulo. `enabled` boolean. Scheduler stores `next_run_at` and execution history; it must reuse existing morning-briefing business logic. Running manually does not silently change the schedule. V1 output is a stored briefing visible in UI, not voice or guaranteed external push delivery.

## 4. Watch module (P5)

```python
class WatchService:
    def create_watch(self, workspace_id: str, parameters: dict) -> dict: ...
    def cancel_watch(self, workspace_id: str, watch_id: str) -> dict: ...
    def list_watches(self, workspace_id: str) -> list[dict]: ...
    def list_alerts(self, workspace_id: str) -> list[dict]: ...
    def run_due_watches(self, now: datetime) -> list[dict]: ...
```

Parameters: `order_id`; `condition_type` (V1 allowlist: `no_progress_by_deadline`, `status_equals`); `deadline_at` (UTC ISO 8601, required for deadline checks); `expected_status` (for `status_equals`); `check_interval_minutes` (positive integer). Reject unsupported conditions explicitly. Each check records timestamp, evidence, observed value, and outcome. Condition evaluation is deterministic; LLM only parses requests. Static CSV snapshots cannot prove real future progress; tests must simulate controlled changes and label them as simulations.

## 5. Action confirmation module (P4)

```python
class ActionService:
    def propose_action(self, workspace_id: str, command: dict) -> dict: ...
    def confirm_action(self, workspace_id: str, proposal_id: str) -> dict: ...
    def reject_action(self, workspace_id: str, proposal_id: str) -> dict: ...
    def get_action_history(self, workspace_id: str) -> list[dict]: ...
```

State machine: `pending_confirmation` -> `executed` or `failed` on confirmation; `pending_confirmation` -> `rejected` on rejection; expired proposals -> `expired`. Confirmation must resolve to one exact proposal, not a vague unbound "yes". Proposals contain normalized action type, target, parameters, expiry, creator/workspace, and idempotency key. Execution MUST re-check authorization and target state, write an audit record, and prevent duplicate side effects under repeated confirmation or retries. Create/update/cancel are mutations requiring confirmation; list/get are read-only.

## 6. Scheduler, persistence and authorization

- Common persistence contract: atomic create/update, list scoped by `workspace_id`, append-only run/audit records, unique execution key (`task_id`, scheduled occurrence). P3/P5 own business repositories; P4 owns proposals/audit repository. Choose one shared backend at integration (suggested: hosted PostgreSQL); implementations use injected repository adapters and in-memory mocks during parallel development.
- A standalone scheduled runner, invoked by an external scheduler, calls both due-task services. It must tolerate delays, retries, and concurrent invocations. GitHub Actions scheduling is best-effort and not exact-time delivery.
- Do not commit keys or `.env`; keep service credentials in deployment secrets. Scheduler access and write actions require server-side authorization. Public UI may be read-only for demonstrations unless authentication is added.
- Scheduled tasks are distinct from `ChatSession` and survive process restarts. No promises of delivery outside the app without an actual notification provider.

## 7. Error codes and acceptance

Suggested errors: `INVALID_ARGUMENT`, `MISSING_FIELD`, `NOT_FOUND`, `AMBIGUOUS_TARGET`, `UNAUTHORIZED`, `CONFLICT`, `ALREADY_EXECUTED`, `EXPIRED`, `STORAGE_ERROR`, `EXECUTION_ERROR`, `UNSUPPORTED_CONDITION`.

Required tests: schedule boundaries and 1/3/7-day intervals; due execution once; persistence after restart; command parsing in Chinese/English; clarification of ambiguous time; watch trigger and no-trigger with changing fixture data; cancellation; unauthorized mutations; confirm/reject/expire; repeated confirmation; audit trace; existing test suite regression.

## 8. Ownership and integration

P1: proactive parsing only; P2: independent evaluation; P3: briefing and its repository; P4: action proposals/confirmation/audit; P5: watches and its repository. The integration owner alone modifies `app/agent/service.py`, `app/agent/turn_service.py`, `app/agent/intents.py`, `app/ui/streamlit_app.py`, and existing shared contracts, after PR review. Each module must provide mockable APIs and tests. Any schema change is proposed in documentation and accepted by the integration owner before implementation.

## 9. Known open decisions (resolve before cloud deployment)

1. Which persistent DB and migration ownership? Recommendation: one hosted PostgreSQL instance, separate tables.
2. Which scheduler runner and service identity? Recommendation: independent job with authenticated DB access.
3. Who can create/cancel tasks on public deployment? Recommendation: authenticated demo operator only; anonymous viewers read-only.
4. Whether notifications are only in-app (V1) or email/push (later). V1 is in-app only.
