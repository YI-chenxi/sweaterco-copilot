# Watches and alerts

`WatchService` creates and cancels order watches, lists stored watches and
alerts, and evaluates due watches in one pass with `run_due_watches(now)`.
Confirmed mutations are routed through the Actions service; a watch request
does not change persistent state until the user confirms it.

Supported conditions are an order reaching an expected status and no recorded
progress by a deadline. The public watch parameters are `order_id`,
`condition_type`, `check_interval_minutes`, `deadline_at`, and
`expected_status`. Evaluation uses an injected order source and stores checks
and matching alerts. Repeated confirmed action IDs reuse their saved result.

The repository has in-memory and SQLite implementations. A trusted external
scheduler must invoke the one-pass runner when checks are due. The included
factory CSV data is a snapshot, so it does not provide live order updates or
external notifications.

Run the focused tests with:

```powershell
python -m pytest -q tests/proactive/watches
```
