"""Prompt and strict schema for enhanced single- and dual-intent parsing."""

from app.agent.enhancement.contracts import InterpretationIssueType
from app.agent.intents import Intent
from app.agent.prompts import SLOT_NAMES

ENHANCED_INTENT_SYSTEM_PROMPT = """
You are the bilingual understanding layer for SweaterCo's factory manager copilot.
Extract routing information only. Never answer the business question, calculate
factory metrics, call tools, or invent missing business parameters.

Supported intents:
- order_status: assess one known order ID.
- production_normality: assess one production stage.
- morning_briefing: return ranked factory attention items.
- order_feasibility: assess a proposed order.
- unknown: no supported business intent clearly applies.

Rules:
1. Return one or two independent tasks in the user's expressed order.
2. Keep every task's slots separate. Never copy a slot between tasks.
3. Use en or zh for the requested response language. Default to English only
   when the language is genuinely unclear.
4. Normalize order IDs, stages, and categories to uppercase. Safe order-ID
   separator variants such as ORD057 mean ORD-057.
   Canonical production stages are KNITTING, ASSEMBLY, WASHING, and PACKING.
   Translate equivalent Chinese stage names and use PACKING, not PACKAGING.
5. A likely typo such as ODR057 must not be silently changed. Leave order_id
   null, mark it missing, and emit a possible_typo issue suggesting ORD-057.
6. Use an ISO YYYY-MM-DD date only when the date is explicit and unambiguous.
   Do not resolve expressions such as next week or month-end by guessing.
7. Missing required values must be null and listed in missing_slots.
8. Unknown tasks must contain no business slots.
9. When conversation context is supplied, a message that only confirms or
   supplies parameters for an existing task should produce a slot_update rather
   than a duplicate new task. The task_id must come from the supplied context.
10. If the target task cannot be identified uniquely, do not guess. Emit an
    ambiguous_reference issue.
11. If the user asks for more than two tasks, return no partial task list and
    emit a too_many_tasks issue so the request can be clarified.
""".strip()


def _slot_properties() -> dict[str, dict[str, object]]:
    return {
        "order_id": {"type": ["string", "null"]},
        "stage": {"type": ["string", "null"]},
        "category": {"type": ["string", "null"]},
        "pieces": {"type": ["integer", "null"], "minimum": 1},
        "requested_delivery_date": {"type": ["string", "null"]},
        "top_n": {"type": ["integer", "null"], "minimum": 1},
    }


TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": [intent.value for intent in Intent],
        },
        **_slot_properties(),
        "missing_slots": {
            "type": "array",
            "items": {"type": "string", "enum": list(SLOT_NAMES)},
        },
    },
    "required": ["intent", *SLOT_NAMES, "missing_slots"],
    "additionalProperties": False,
}

SLOT_UPDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
        **_slot_properties(),
    },
    "required": ["task_id", *SLOT_NAMES],
    "additionalProperties": False,
}

ISSUE_SCHEMA = {
    "type": "object",
    "properties": {
        "issue_type": {
            "type": "string",
            "enum": [issue.value for issue in InterpretationIssueType],
        },
        "field": {"type": ["string", "null"]},
        "original_value": {"type": ["string", "integer", "null"]},
        "suggested_value": {"type": ["string", "integer", "null"]},
        "decision_index": {"type": ["integer", "null"], "minimum": 0},
        "task_id": {"type": ["string", "null"]},
    },
    "required": [
        "issue_type",
        "field",
        "original_value",
        "suggested_value",
        "decision_index",
        "task_id",
    ],
    "additionalProperties": False,
}

ENHANCED_INTENT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "language": {"type": "string", "enum": ["en", "zh"]},
        "is_ambiguous": {"type": "boolean"},
        "tasks": {
            "type": "array",
            "items": TASK_SCHEMA,
            "maxItems": 2,
        },
        "slot_updates": {
            "type": "array",
            "items": SLOT_UPDATE_SCHEMA,
            "maxItems": 2,
        },
        "issues": {
            "type": "array",
            "items": ISSUE_SCHEMA,
            "maxItems": 4,
        },
    },
    "required": [
        "language",
        "is_ambiguous",
        "tasks",
        "slot_updates",
        "issues",
    ],
    "additionalProperties": False,
}
