# Confirmed actions

`ActionService` stores proposed changes and audit events. A proposed briefing
or watch change remains inactive until the user confirms it. Confirmation
checks the authenticated actor, workspace permission, original creator, and
target version before the business executor runs. Repeated confirmation of a
completed proposal returns its stored receipt.

The public methods are `propose_action(workspace_id, command)`,
`confirm_action(workspace_id, proposal_id)`,
`reject_action(workspace_id, proposal_id)`, and
`get_action_history(workspace_id)`. Mutation results include `ok`, `status`,
`id`, `data`, `error_code`, and `message`. History is scoped to the authorized
workspace.

The module uses `SQLiteActionRepository` for local persistence. The composed
application supplies real briefing and watch executors. A standalone offline
simulation is also available; it does not change factory data:

```powershell
python -m app.agent.proactive.actions.demo
python -m pytest -q tests/test_confirmed_actions.py
```

SQLite files require persistent storage. The repository does not provide
external scheduling or notification delivery.
