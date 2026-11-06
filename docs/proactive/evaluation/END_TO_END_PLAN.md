# Proactive end-to-end acceptance plan

Run after P1, P3, P4, and P5 modules are integrated behind the documented contracts. Use isolated temporary repositories and deterministic clocks/executors. Do not connect production data or perform real order mutations.

1. Run `python -B -m pytest -p no:cacheprovider tests/proactive -q` and each feature owner's tests.
2. Feed every case in `tests/proactive/cases.json` to P1; serialize normalized `ProactiveCommand` results and score them with `tests/proactive/evaluate.py`.
3. For briefings, test 1/3/7-day cadence anchored to `start_date`, local-time-to-UTC conversion, exact due boundary, one second before due, disabled schedules, retry, and process restart persistence.
4. For watches, test both supported conditions with a mutable fixture: no trigger before condition, trigger when condition becomes true, cancellation, unsupported condition rejection, evidence fields, workspace isolation, and restart persistence. Label fixture changes as simulated.
5. For actions, verify create/update/cancel only produce proposals; exact confirmation executes once; rejection, expiry, unauthorized workspace, stale target, ambiguous “yes”, retry, and audit trace produce no duplicate or unauthorized side effects.
6. Run old business workflow regression tests and the complete test suite after integration. Report mock results and any live model measurements separately.

Record the tested commit, runtime and dependency versions, case-set hash, test command and result, and known limitations. A failed acceptance item must remain visible in the report; do not weaken ground truth to make a run pass.
