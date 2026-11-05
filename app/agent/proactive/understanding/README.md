# P1 proactive command understanding

## Tool contract

- **Question answered:** Is this message a supported proactive command, and which
  explicit parameters and existing target does it name?
- **Input:** one English or Chinese user message, optional context containing
  `briefings`, `watches`, and `proposals` arrays of `{ "id": "..." }` entries, and
  an injected `StructuredLLMClient`.
- **Output:** `ProactiveCommand` with `command_type`, `task_type`, normalized
  `parameters`, `target_id`, `missing_fields`, `language`, and optional `reference_text`.
  Call `.to_dict()` for the JSON-compatible P1 interface.
- **Deliberately does not:** run tools, retrieve facts, calculate feasibility,
  create or modify persistent records, authorize users, confirm actions, or send
  messages. `missing_fields` or `unknown` means no executable mutation is ready.

```python
from app.agent.proactive.understanding import ProactiveCommandParser

parser = ProactiveCommandParser(llm_client)
command = parser.parse("从2026年11月6日开始每3天早上8点生成晨报")
payload = command.to_dict()
```

The parser accepts one command per message. A scheduled briefing is distinct
from an immediate morning-briefing question. A standing watch is distinct from
an immediate order-status question. Confirm/reject requires a proposal ID that
is present in context; with multiple proposals, the ID must also appear in the
user message. Target IDs must be UUIDs, matching P4's action interface.
Ambiguous targets remain missing. Watch updates and order-note or
message-send actions are unsupported by the current V1 service contract.

Times use `Asia/Singapore` by default. A deadline is converted to UTC only when
the source message includes an explicit date and a time. Relative dates and
ambiguous times remain missing. `reference_text` is left null to avoid copying
possible secrets into persistent audit records. The model may assist with language extraction,
but final value validation is deterministic. Model failures return `unknown`.

## Integration handoff

The integration owner routes `create`, `update`, and `cancel` through P4's action
proposal and confirmation service. `list` and `get` are read-only. Before wiring,
agree on the context ID format, whether `get` includes briefing history, and
whether P5 will support watch updates. This module does not edit shared agent or
UI files. The integration baseline for this branch is `fec149e`.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest tests\proactive\test_understanding.py -q
.\.venv\Scripts\python.exe -m ruff check app\agent\proactive\understanding tests\proactive\test_understanding.py
```

Tests use queued mock model outputs. They verify parsing and validation, not
live-model accuracy. P2's evaluation and integration tests are separate.
