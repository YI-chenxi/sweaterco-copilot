# Person 2 NLU Evaluation

Person 2 owns the ground-truth cases, evaluation runner, reports, and evaluator tests. The runner measures parser output only; it does not execute A1/A2 tools. It reuses the existing enhanced parser and does not change parser or prompt behavior.

## Ground truth

`intent_cases.json` contains 54 reviewed cases. It preserves the original 48 annotations and adds six cases for a likely typo, invalid quantity, invalid date, partially specified two-task request, too many tasks, and a context slot update. The unresolved `eval-055` ambiguous-reference example is excluded. Do not revise expected answers to improve scores.

## Running evaluation

Set `MODEL_PROVIDER=openai`, `OPENAI_MODEL`, and `OPENAI_API_KEY` using the local environment or `.env`. The runner does not print or report the key. It refuses to overwrite existing output files by default.

Enhanced parser, all 54 cases (54 model calls):

```powershell
.\\.venv\\Scripts\\python.exe -m tests.enhancement.understanding.evaluation
```

Legacy comparison (54 enhanced calls and 40 legacy calls):

```powershell
.\\.venv\\Scripts\\python.exe -m tests.enhancement.understanding.evaluation --compare-legacy
```

The legacy parser is scored only on shared capabilities: ordered intent, slots, and missing slots. Cases are limited to single-turn, single-task examples with no issue labels. Language, issue types, context updates, and other enhanced capabilities are excluded from the legacy report and do not affect legacy pass/fail. Both runs use the same model client settings, while their parser implementations and system prompts differ; reports include each prompt SHA-256 to keep that distinction visible. Results can still vary between requests.

Use `--limit N` for a smaller run. Supply distinct `--json-report`, `--markdown-report`, `--legacy-json-report`, and `--legacy-markdown-report` paths for repeated experiments. `--overwrite` explicitly replaces requested outputs. The CLI checks all output paths before model setup or calls.

## Metrics and failures

Exact-case accuracy requires all applicable checks to pass. Intent sequence is positional; task count differences are errors. Slot and missing-slot precision, recall, and F1 are micro-averaged. Slot updates match by task ID, slot, and canonical value; duplicates are false positives. Parser exceptions are marked as failures and isolated so later cases still run; sensitive exception detail is omitted.

Mock tests check evaluator behavior and are not model-accuracy measurements. Live reports include timestamp, provider, model, prompt and parser identifiers, prompt hashes, Git commit, Git worktree dirty status, and dataset hash. For reproducible results, run from a clean committed worktree; if local edits are present, the report marks that state. The checked-in historical `evaluation/nlu_evaluation_report.*` files describe an earlier 48-case run from 2026-10-27; they are not results for this 54-case dataset.

No live run has been executed for the 54-case dataset. Enhanced-only requires 54 calls. Legacy comparison adds 40 calls. API charges depend on model and token use; obtain explicit authorization before running either live command.

## Known limits

- `eval-055` is not included because its expected interpretation is unresolved.
- Issue types are scored, but issue field/original/suggested values are not fully scored.
- Legacy comparison excludes context, updates, multi-task requests, issues, and enhanced ambiguity/outcome behavior.
- Parser and prompt revisions differ between legacy and enhanced systems, so score differences are not attributable to model choice alone.
- Live model behavior may vary between calls.

## Validation and ownership

Run from the repository root:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider tests\enhancement\understanding -q
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -q
.\.venv\Scripts\python.exe -m ruff check tests/enhancement/understanding app/agent/enhancement/understanding/evaluation.py
git diff --check
```

Feature branch: `feat/nlu-evaluation`. PR base: `integration/intent-enhancement`. No commit, push, or PR has been created. Shared contracts are owned by Person 3; parser implementation and prompts are owned by Person 1. Raise proposed shared-contract changes with Person 3 instead of editing shared contracts here.
