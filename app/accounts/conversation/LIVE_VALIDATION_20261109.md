# Person 4 real database validation 2026 11 09

Dependency baseline: P1 f5dd3d2, P2 e67aa3c, P3 0d2f90f.
P4 implementation baseline: ade6455. This delivery updates only test synchronization
and validation documentation; no production business implementation changes were
necessary after the real database checks.

## Environment and isolation

User-authorized Supabase PostgreSQL over TLS. No endpoint, password, bearer token
or account secret is recorded here. Credentials were loaded from a configuration
file outside the repository, not from test CLI arguments.

Each real run created its own random p4_test_ schema. The unchanged P3 001/002
migrations were applied only in that namespace. Test users, workspaces, sessions,
threads, messages, request receipts and agent states all belonged to that schema.
The fixture dropped its own namespace afterward. Read-only verification after
both runs found zero remaining p4_test_ namespaces.

Read-only checks also confirmed:
- Existing public 001 and 002 migration checksums match the pinned P3 sources.
- Completion fingerprint, completed revision and assistant message ID columns exist.
- Existing automatic private-workspace provisioning trigger exists.
- Existing public migration versions were unchanged after the tests.

## Actual results

| Run | Scope | Observed result |
| --- | --- | --- |
| Local full regression | Combined P1/P2/P3/P4 checkout | 741 passed, 13 skipped, 0 failures/errors |
| Supabase first run | All seven P4 live cases | 6 passed, 1 failed in 940.22 seconds |
| Supabase concurrency recheck | Same concurrency case, synchronization budget increased | 1 passed, 6 deselected in 181.88 seconds |

The first concurrency failure was Event.wait(15) expiring before the cloud-backed
worker reached the synchronization point. Its traceback confirmed the wait
expired, rather than a duplicate Agent execution or ownership failure.
The synchronization waits/future join budget were changed to 180 seconds. No
database assertion, authorization rule, conflict expectation or execution-count
assertion was removed or weakened.

All seven DISTINCT live acceptance scenarios have now passed after that fix:
1. Register/login, save a partially completed two-task request, logout, construct
   fresh backend services and reconnect/login, restore state, supply the missing
   delivery date, and execute only the remaining task with evidence preserved.
2. Two-account thread listing/read/rename/delete/send isolation and authorized CRUD.
3. Replay an earlier request after later turns without re-executing or rolling back.
4. During one request's Agent execution, a second worker cannot execute the same
   request ID or another new request on that thread; Agent counts remain one/zero.
5. Agent failure preserves the pending user message with failed/reconciliation state
   and prevents blind retry.
6. A real committed database turn followed by simulated acknowledgement loss can be
   replayed from its persisted receipt without another Agent execution.
7. Logout during Agent execution prevents assistant/state commit; a new login sees
   only the preserved unresolved request.

These runs are reported separately. Their counts are not added to produce a
fictional single full-suite result. The 13 skips in the local run were seven P4
live cases (executed separately above) and six original P3 local-PostgreSQL cases.
The latter six were not executed against Supabase in this delivery.

## Checks and remaining integration work

P4 Ruff lint/format and Python 3.10 syntax checks passed. Changed P4 files were
scanned for the actual configured URL, password and account secret, with no
matches. Real values remain outside Git.

Suite times include test user registration, many fresh TLS database connections,
repeated authorization, and cleanup on the current network. These are not a
per-chat performance benchmark or a Streamlit Cloud latency guarantee.

No live LLM calls were made. Model outputs were queued test outputs; business
tools and the account/chat PostgreSQL repositories were real.
No frontend files, browser cookies, external schedulers or proactive business
tables were part of this account-system validation.

P5 still needs to integrate the account backend, apply P2's approved safe cookie
instructions and validate refresh/reopen/logout/account-switch behavior in the
deployed browser. The deployer must provide the same stable ACCOUNT_SECRET across
instances in addition to DATABASE_URL. Never commit either value.
