# Annotation adjudication record

Run: 2026-11-06, model `gpt-6-luna`, P1 parser revision `14390cc1bf45f95247ef4af6c56fa603259c50ce`.

The original frozen case file used for the model calls is preserved as `cases_as_run.json` with SHA-256 `d1ba00367fce7f329bc345769eb13b959ff5d0f281b88dea641be39d5f02ed38`. The utterances and contexts have not changed in the current case file; only five expected labels were adjudicated after the first scoring pass. No parser, prompt, model, or service code was changed. Predictions can therefore be rescored against the reviewed labels without additional model calls, but the reviewed score is exploratory because annotation review happened after seeing initial outcomes. Use a newly frozen blind set for confirmatory accuracy.

| Case | Original label issue | Reviewed label basis |
|---|---|---|
| `p1-seed-03-immediate-briefing-is-not-schedule` | Accidentally carried a watch-create label from a neighboring case. | P1 seed says an immediate briefing is an existing one-off business request, not schedule creation; expected `unknown`. |
| `p1-seed-04-ambiguous-briefing-update` | Omitted explicit daily interval and represented the ambiguous hour as an empty update. | The request explicitly says daily (`interval_days=1`); “7 o'clock” still requires `local_time` clarification. |
| `p1-seed-10-unsupported-risk-watch` | Marked `unknown`, although P1 can safely identify watch intent while withholding unsupported condition fields. | Expected create/watch with only the recognized order ID, missing `condition_type` and `check_interval_minutes`; proposal must remain blocked. |
| `p1-seed-12-negation-never-confirms` | Expected only `unknown`. | P1 seed permits rejection or clarification; the reviewed single label is `reject` bound to the sole proposal in context. |
| `p2-holdout-26-watch-status-missing-expected-value` | Asserted `status_equals` despite no expected status being named. | `status changes` is not itself an allowlisted equality value; expected only order ID, with condition and interval missing. |

The original `report.json` is retained as the initial score (19/33, before label review). `report_adjudicated.json` is the reviewed exploratory score (23/33). `metadata_adjudicated.json` records both hashes and the post-run review state.
