# Group 1 — Natural Language Understanding

## Goal

Improve the Assistant's ability to understand informal, ambiguous, and bilingual user requests.

## Responsibilities

- Intent classification and slot extraction.
- Natural language variations and prompt improvement.
- Evaluation dataset and accuracy measurement.
- Error analysis and iterative improvement.

## Planned Modules

- `parser.py`: Enhanced natural language understanding.
- `evaluation.py`: Evaluation utilities, if needed.
- `tests/enhancement/understanding/`: Evaluation cases and automated tests.

## Interface

Input: User message and optional conversation context.

Output: `UnderstandingResult` defined in `app.agent.enhancement.contracts`.

Reuse existing `IntentDecision`, `Intent`, and `ResponseLanguage`.

## Rules

Do not modify existing A1/A2 business calculations, Streamlit UI, or API configuration.

Coordinate shared interface changes through `INTERFACE_CONTRACT.md`.