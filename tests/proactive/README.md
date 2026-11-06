# Proactive agent evaluation

This package is Person 2's independent acceptance harness for scheduled briefings, watches, and confirmed actions. The ground-truth cases derive from Person 1's `docs/proactive/P1_EVALUATION_SEEDS.md` and are checked against the parser contract in `docs/proactive/P1_HANDOFF.md` and `docs/proactive/P1_P4_INTEGRATION_NOTES.md` on `origin/feat/proactive-intent-parser`. The harness scores normalized command outputs and provides deterministic mock service contract tests; it does not implement P1/P3/P4/P5 services.

## Files and ownership

- `cases.json`: 15 P1 seed utterances, one multiple-pending-proposals safety extension, and 17 independent P2 holdout cases (33 total). Each case records context IDs, normalized expected output, missing fields, language, source seed, and whether it may enter P4's proposal flow.
- `cases_blind_v2.json`: 30 newly frozen cases (15 Chinese, 15 English), separated from the adjudicated first set; SHA-256 and raw predictions are stored under `evaluation/proactive_live_20261106_blind_v2/`.
- `evaluate.py`: exact-field scorer and failure taxonomy. Missing outputs are failures.
- `run_live_evaluation.py`: invokes P1's real parser serially and writes predictions, score, and metadata without calling business services.
- `test_evaluate.py`: deterministic scorer tests.
- `mock_services.py` and `test_mock_contracts.py`: P2-owned deterministic mock service fakes plus 41 contract tests for briefing schedules, watches, and confirmed actions. Coverage includes time/date/timezone boundaries, invalid arguments, authorization scoping, injected storage/execution failures, retry behavior, and idempotency. These exercise test doubles only; they do not run P3/P4/P5 implementation branches.
- `../../docs/proactive/evaluation/FAILURE_ANALYSIS.md`: known failure risks and the post-integration triage plan.
- `../../docs/proactive/evaluation/END_TO_END_PLAN.md`: tests to execute when feature modules are integrated.

## Run

```powershell
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider tests\proactive -q
```

Score a parser output file (JSON object keyed by case ID):

```powershell
.\.venv\Scripts\python.exe tests\proactive\evaluate.py --predictions path\to\predictions.json --output evaluation\proactive_report.json
```

Run the live parser evaluation without executing any business actions (requires a real API key and may incur model usage charges):

```powershell
.\.venv\Scripts\python.exe tests\proactive\run_live_evaluation.py --parser-root <P1 parser checkout> --output-dir evaluation\proactive_live_<date>
```

The runner records model and parser/prompt hashes, omits credentials, isolates per-case failures, and writes predictions, a scored JSON report, and metadata. The 2026-11-06 run made 32 calls with zero request failures. Initial scoring was 19/33 (57.6%). Review found five annotation errors; `evaluation/proactive_live_20261106/cases_as_run.json` and `ANNOTATION_ADJUDICATION.md` preserve the original dataset and changes. Against the reviewed labels, the same predictions score 23/33 (69.7%). This is exploratory because annotation review followed the initial scoring; a new blind holdout is required for confirmatory accuracy. No parser or prompt tuning was performed.

The evaluator is deterministic and makes no model calls. The first 33-case score is exploratory because five labels were reviewed after seeing predictions. The separate 30-case v2 set was frozen before its 30 live calls; it scored 17/30 (56.7%) with no model-call failures. V2 results are blind to parser outputs during case authoring, but remain a small, single-annotator acceptance sample rather than a statistically representative benchmark. Do not pool the two scores into one accuracy figure.

## Contract mapping and limits

Cases cover P1 command fields (`command_type`, `task_type`, `parameters`, `target_id`, `missing_fields`, `language`) and P3/P5/P4 semantics described in `docs/proactive/INTERFACE_CONTRACT.md`. The 41 tests in `test_mock_contracts.py` validate P2 mock contracts, not production service branches. No P3/P4/P5 feature branch tests were rerun for this P2 delivery. Production service behavior, cross-branch integration, shared persistence, and end-to-end regression acceptance remain unverified here. No UI, existing business logic, shared contract, or service entry point is changed.
