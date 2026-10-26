# Group 1 — Natural Language Understanding

## Goal

Convert an informal English or Chinese user message into validated routing data
without executing business tools or inventing missing parameters.

The implementation supports:

- One or two independent business tasks in the order expressed by the user.
- Task-specific slot extraction with no cross-task slot sharing.
- Safe deterministic normalization for order IDs, dates, quantities, stages,
  and categories.
- Structured issues for values that need clarification.
- Context-aware updates to an existing conversation task.
- Deterministic confirmation of one unambiguous correction candidate.

## Entry Point

```python
from app.agent.enhancement.understanding import EnhancedIntentParser

result = EnhancedIntentParser(llm_client).parse(
    user_text,
    context=conversation_state,
)
```

The return value is `UnderstandingResult` from
`app.agent.enhancement.contracts`. It can contain:

- `decisions`: newly identified tasks.
- `slot_updates`: validated updates for existing task IDs.
- `issues`: structured problems requiring clarification.
- `is_ambiguous`: whether unresolved issues remain.
- `language`: the response language detected for this turn.

## Files

- `parser.py`: validates model output and builds public contract objects.
- `prompts.py`: bilingual extraction rules and strict response schema.
- `normalizers.py`: deterministic normalization that does not call the model.

## Order-ID Policy

Formatting variants that preserve the letters and digits are safe to normalize:

| Input | Result |
| --- | --- |
| `ORD057` | `ORD-057` |
| `ord-057` | `ORD-057` |
| `ORD 57` | `ORD-057` |

A letter transposition is not treated as formatting. For example, `ODR057`
produces a `possible_typo` issue whose suggested value is `ORD-057`; the order
ID remains missing until the user confirms it.

When the pending conversation contains exactly one valid typo candidate, a
reply such as `是的` or `yes` becomes a `SlotUpdate` for that task without a
second model call. If multiple candidates exist, the parser asks the model to
resolve the reference instead of guessing.

## Multi-task Policy

The parser accepts at most two new tasks per message. Each decision is built and
validated independently, and only slots allowed for that intent are retained.
More than two tasks must produce a `too_many_tasks` issue rather than a partial
task list.

## Context Updates

Conversation context is serialized with task IDs, statuses, missing slots, and
unresolved issues. A parameter-only follow-up should return a `SlotUpdate`
targeting an existing task ID. Unknown or ambiguous targets become
`ambiguous_reference` issues.

`conversation.memory.apply_slot_update()` remains responsible for applying the
update. When a field is updated, its matching issue is cleared and readiness is
recomputed. The parser itself never mutates conversation state.

## Validation and Failure Behaviour

- Required slots are recalculated locally; model-declared completeness is not
  trusted.
- Unsupported slots are discarded from each intent.
- Relative or invalid dates remain unresolved.
- Invalid integers, dates, and IDs become `invalid_format` issues.
- Model or schema failures fall back to `Intent.UNKNOWN` in the user's language.
- Empty input returns `Intent.UNKNOWN` without a model call.

## Test Commands

```powershell
.\.venv\Scripts\python.exe -m pytest tests\enhancement\understanding -q
.\.venv\Scripts\python.exe -m pytest tests\enhancement -q
.\.venv\Scripts\python.exe -m ruff check app\agent\enhancement tests\enhancement
```

## Ground-truth Evaluation

`evaluation/nlu_cases.json` contains 48 cases, with six cases in each of these
categories: standard, colloquial, Chinese, English, mixed-language, unknown
intent, missing slots, and dual intent.

Run the live evaluation with the model configured in `.env`:

```powershell
.\.venv\Scripts\python.exe -m app.agent.enhancement.understanding.evaluation
```

The evaluator measures exact intent sequence, response language, task-specific
slots, missing slots, issue types, and complete-case accuracy. It writes both
`evaluation/nlu_evaluation_report.json` and
`evaluation/nlu_evaluation_report.md`.

On 2026-10-27, the checked-in dataset scored 48/48 exact matches with the
configured `gpt-6-luna` model after canonicalizing the `PACKAGING` stage alias
to the system value `PACKING`.

## Integration Boundary

This module does not change `AgentService.handle_text()`, business calculations,
Streamlit UI, or API configuration. A later integration step can connect the
result to conversation memory and planning through a backward-compatible entry
point.

The additions to `app.agent.enhancement.contracts` are public interface changes
and were approved by the shared-contract owner on 2026-10-27.
