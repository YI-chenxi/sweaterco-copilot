# Enhanced NLU Evaluation Analysis

## Scope

This evaluation checks the enhanced understanding layer against 48 ground-truth
messages. The corpus contains six cases in each required category:

- Standard expressions
- Colloquial expressions
- Chinese
- English
- Mixed Chinese and English
- Unknown intent
- Missing required slots
- Dual intent

The live run used the `gpt-6-luna` model configured in the local environment.
No API credentials are stored in the dataset or reports.

## Scoring

Every case is scored using exact comparison of:

1. Ordered intent sequence
2. Response language
3. Task-specific slots
4. Missing slots
5. Interpretation issue types

A case is an exact match only when all five components match. This deliberately
penalizes partially correct parsing, cross-task slot leakage, and incorrect
language routing.

## Initial Run

The first live run scored 46/48 exact matches (95.8%). Intent sequence, missing
slots, and issue types were all 100% correct. Two discrepancies were found:

1. `请看看包装工序是否正常。` produced `PACKAGING`, while the downstream system
   requires the canonical stage value `PACKING`.
2. `把这段文字翻译成英文。` produced response language `en`, while the initial
   annotation expected `zh`.

The first discrepancy was a real normalization gap. The parser depended on the
model to choose the downstream system's exact stage token. The second was a
ground-truth error: the message explicitly requests English output, and the
language contract defines the field as the requested response language.

## Changes After Error Analysis

- Added deterministic stage normalization for all supported stages.
- Added aliases for Chinese stage names.
- Canonicalized `PACKAGING` and `包装` to `PACKING`.
- Rejected unsupported stage values instead of forwarding them to tools.
- Clarified the prompt's canonical production-stage vocabulary.
- Corrected the translation case's expected response language to English.

## Final Run

The full 48-case evaluation was rerun after the changes.

| Metric | Result |
| --- | ---: |
| Exact case match | 48/48 (100.0%) |
| Intent sequence | 100.0% |
| Response language | 100.0% |
| Slots | 100.0% |
| Missing slots | 100.0% |
| Issue types | 100.0% |

Every category scored 6/6 exact matches.

## Limitations

- Six cases per category are enough for a first acceptance test, but they do not
  represent every possible user expression.
- The report records one live run. Repeated trials would better measure model
  output stability.
- Multi-turn confirmation, typo correction, invalid numeric values, and requests
  containing more than two tasks are covered by deterministic unit tests rather
  than this eight-category live corpus.
- This evaluation measures understanding output only; it does not execute A1/A2
  business tools or evaluate final response wording.

## Reproduction

```powershell
.\.venv\Scripts\python.exe -m app.agent.enhancement.understanding.evaluation
```

Machine-readable results are stored in `evaluation/nlu_evaluation_report.json`.
The generated summary is stored in `evaluation/nlu_evaluation_report.md`.
