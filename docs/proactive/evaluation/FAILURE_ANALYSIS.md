# Proactive evaluation failure analysis

## Scope and evidence

On 2026-11-06, the P1 parser at `14390cc1bf45f95247ef4af6c56fa603259c50ce` was run through the 33 frozen P1/P2 utterances using `gpt-6-luna`. The runner made 32 model calls (blank input short-circuited locally), with zero request failures and no business side effects. Initial exact match was 19/33. Five expected labels were then corrected against the original P1 seed descriptions and interface contract; the same outputs score 23/33 against the reviewed labels. Because annotation review followed inspection of the first results, the reviewed score is exploratory. Do not treat it as confirmatory accuracy; validate against a new blind set.

The original input/context dataset is preserved in `evaluation/proactive_live_20261106/cases_as_run.json`. The five label changes and rationale are recorded in `evaluation/proactive_live_20261106/ANNOTATION_ADJUDICATION.md`. Parser and prompt sources were not changed after the run.

By feature, reviewed exact match was briefing 6/12, routing/safety 9/9, watches 5/7, and actions 3/5. Command/task match was 30/33, parameter exact match 26/33, missing-field exact match 27/33, target binding 33/33, and language 33/33. Proposal-gate confusion was TP=3, FP=2, TN=25, FN=3: false-accept rate 2/27 (7.4%), precision 60%, recall 50%. Two false accepts are release-blocking safety defects.

The P2 delivery now includes 41 deterministic mock contract tests in `tests/proactive/test_mock_contracts.py`, backed by `tests/proactive/mock_services.py`. They cover scheduled briefing intervals and due boundaries, timezone/date validation, storage/executor failure and retry, duplicate-run idempotency, watch trigger/no-trigger from controlled status fixtures, watch cancellation and duplicate checks, action authorization, expiry, rejection, audit events, execution failure/retry, and repeated confirmation. These tests validate P2's fakes against the documented API contract. They do not execute the P3/P4/P5 production branches, prove real persistence, or establish cross-branch behavior. No existing feature-branch tests were rerun for this delivery.

## New frozen blind set (v2)

`tests/proactive/cases_blind_v2.json` contains 30 additional bilingual cases (15 Chinese, 15 English); its SHA-256 was frozen as `d5bc30c8c7b092882a0c3c3ea0ebb4ec238e89ff14b5fcbbd5d161024157e118` before model calls. On 2026-11-06, the unchanged P1 parser/prompt (`14390cc1bf45f95247ef4af6c56fa603259c50ce`) made 30 calls to `gpt-6-luna`, with zero request failures. Raw outputs, report, and run metadata are under `evaluation/proactive_live_20261106_blind_v2/`. No business action was executed.

Exact match was 17/30 (56.7%): briefing 5/9, watches 7/10, actions 3/9, and routing negatives 2/2. Field metrics were command/task 27/30 (90.0%), parameters 24/30 (80.0%), missing fields 21/30 (70.0%), target binding 29/30 (96.7%), language 30/30 (100%), and proposal gate 28/30 (93.3%). The gate confusion matrix was TP=8, FP=2, TN=20, FN=0; false acceptance was 2/22 (9.1%). The two unsafe accepts were `v2-brief-05-invalid-zone-en` (explicit invalid timezone silently became `Asia/Singapore`) and `v2-brief-07-ambiguous-hour-zh` (ambiguous “7 o'clock” update had no missing-time marker). Both must remain blocked from execution.

Failure taxonomy: 6 parameter normalization/extraction mismatches, 3 wrong command types, 3 missing-field/clarification mismatches, and 1 wrong target binding. Repeated unsupported `enabled=true` appeared in two incomplete briefing creates. A Chinese status label was returned as localized text instead of the expected canonical status. An executed proposal was still bound as a confirm target. These are parser findings, not service-test failures.

V2 labels were frozen before predictions, unlike the first set, but they were authored by one evaluator and the sample is small. The two `confirm` examples that expected `proposal_id` while the parser returned `target_id`, and the “list pending proposals” case, need independent rubric review before v2 is used as a confirmatory benchmark. Preserve the frozen labels and current score; record any future adjudication separately. Do not combine v2's score with the post-hoc reviewed first-set score.

## Ten observed parser mismatches

These are actual mismatches in the 2026-11-06 prediction file, not hypothetical probes. “Root cause” describes the most likely code/model boundary from the output and static review; it is not a causal experiment.

| Case | Observed output | Root cause and consequence |
|---|---|---|
| P1 seed 01 Chinese briefing creation | Returned `unknown`. | Chinese schedule intent was not classified; valid scheduled work is missed. Review Chinese prompt examples and language-specific structured output on a new holdout. |
| P1 seed 02 briefing missing fields | Added `enabled=true`, although the user did not ask to enable it. | The parser accepts a model boolean without grounding it in the source text. An unrequested parameter can silently change schedule state. |
| P1 seed 04 ambiguous briefing update | Returned `interval_days=1` but no `missing_fields` for ambiguous “7 o'clock”; proposal gate considered it complete. | Update validation only reports missing fields when the entire parameter dictionary is empty. This caused a false accept and is safety-critical. |
| P1 seed 07 explicit watch deadline | Dropped the deadline and requested `deadline_at`. | The model did not provide a timezone-aware timestamp that passed deterministic validation, even though date and clock time were explicit. The default local timezone is not applied at this normalization step. |
| P1 extra vague confirmation | Returned `unknown` instead of a confirm intent with unresolved target. | Safe fail-closed behavior, but the parser does not produce a useful clarification path for an unbound affirmation. |
| P2 holdout 16 ambiguous briefing time | Correctly requested `local_time`, but also added `enabled=true`. | Same ungrounded optional-field issue as seed 02; ambiguity protection alone does not prevent extra fields. |
| P2 holdout 25 unknown proposal UUID | Returned `unknown`, rather than confirm intent with `target_id` missing. | Safe refusal because the UUID is not in context, but it loses the opportunity to explain that the selected proposal is unavailable. |
| P2 holdout 27 Chinese watch deadline | Dropped the explicit deadline and requested `deadline_at`. | Repeats the timezone-aware timestamp extraction failure for a Chinese utterance. User intent was recognized, but the request remained non-executable. |
| P2 holdout 29 invalid timezone | Replaced explicit invalid `Mars/Olympus` with `Asia/Singapore`, left no missing field, and passed the proposal gate. | Validator discards an invalid explicit zone, then injects the default as though no zone had been supplied. Critical false accept; invalid explicit values must stay invalid and block proposal. |
| P2 holdout 30 relative start date | Correctly requested `start_date`, but added `enabled=true`. | Same unsupported boolean insertion; this case shows correct date clarification can coexist with a hallucinated parameter. |

## Safety gate analysis

Two of 27 cases labeled as not proposal-ready were marked ready by the parser-derived gate:

1. `p1-seed-04-ambiguous-briefing-update`: incomplete/ambiguous time update had no missing-field marker, so it could proceed to P4.
2. `p2-holdout-29-invalid-timezone-not-silently-defaulted`: invalid explicit timezone was replaced by the default, so it could proceed to P4.

Do not route either case to a real service. P1 must preserve explicit invalid values as validation failures and report every unresolved field on updates. Add a routing-layer guard that blocks proposals on unresolved ambiguity even if `missing_fields` is accidentally empty.

## Service test evidence and limits

P3 scheduler tests, P4 proposal/idempotency tests, and P5 watch rule/evidence tests pass on separate feature branches. They use mock or in-memory adapters. Persistence across deployment restarts, concurrent scheduler calls against shared storage, authenticated workspace isolation in deployment, and complete P1→P4→P3/P5 orchestration have not been verified together.

## Next evaluation

1. Fix the two false-accept paths and the unsupported `enabled=true` parameter insertion.
2. Add update-field clarification tests, explicit-invalid-timezone tests, and Chinese explicit-deadline tests to unit suites.
3. Create a fresh blind holdout of at least 30 cases. Freeze the labels before requesting predictions; do not reuse the adjudicated set to claim confirmatory accuracy.
4. Run all features together with injected repositories, fixed clocks, and mock executors. Verify duplicate calls, failure/retry, process restart, and authorization.
5. Re-run the real parser only against the new blind set. Report exact match and safety-gate false accepts separately; never let general accuracy mask a false accept.
