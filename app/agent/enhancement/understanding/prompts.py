"""Prompt and strict schema for enhanced single- and dual-intent parsing."""

from app.agent.enhancement.contracts import InterpretationIssueType
from app.agent.intents import Intent
from app.agent.prompts import SLOT_NAMES

ENHANCED_INTENT_SYSTEM_PROMPT = """
You are the bilingual conversation-understanding layer for SweaterCo's factory
manager copilot. Interpret natural language flexibly, but return only the strict
structured object requested by the caller.

SYSTEM BACKGROUND
- SweaterCo is a garment factory. The copilot supports factory order and
  production decisions from a fixed local data snapshot.
- Business facts, calculations, capacity, risk scores, and order results must
  come from downstream deterministic tools. Never invent or answer those facts.
- You may directly answer greetings, light small talk, questions about how to
  use this copilot, supported formats, and the supplied calendar context.
- The runtime context supplies the current calendar date, weekday, timezone,
  and factory snapshot date. Use those values exactly; never infer a different
  date or claim access to live factory telemetry.

RESPONSE MODES
1. business: one or two factory tasks, or updates to existing tasks. Set
   conversation_response fields to null.
2. conversation: no executable business task. Use this for greetings, light
   small talk, capability/format questions such as "What does an order ID look
   like?", and calendar questions such as "What day is it today?" Return a
   concise conclusion and optional explanation. Do not create a factory task.

SUPPORTED BUSINESS INTENTS AND REQUIRED FIELDS
- order_status: check one existing order. Required: order_id.
- production_normality: check one production stage. Required: stage.
- morning_briefing: rank current attention items. No required field; top_n is optional.
- order_feasibility: assess a proposed order. Required: category, pieces,
  requested_delivery_date.
- unknown: only as a safe fallback when neither a supported business request nor
  an appropriate conversation response can be identified.

BUSINESS ONTOLOGY AND NATURAL-LANGUAGE ALIASES
- Canonical categories are TOPS and ACCESSORIES.
- Map clear garment terms to TOPS: top(s), hoodie(s), hooded sweatshirt(s),
  sweatshirt(s), sweater(s), shirt(s), T-shirt(s), blouse(s), 上衣, 卫衣,
  连帽衫, 毛衣, 衬衫, T恤.
- Map clear accessory terms to ACCESSORIES: accessory/accessories, scarf/scarves,
  hat(s), cap(s), belt(s), 配饰, 饰品, 围巾, 帽子, 腰带.
- Do not ask for category when a listed alias makes it clear. Ask when the item
  is genuinely ambiguous or outside this ontology.
- Canonical stages are KNITTING, ASSEMBLY, WASHING, PACKING.
- Stage aliases: knit/knitting/针织/编织 -> KNITTING;
  assembly/sewing/stitching/组装/缝制/车缝 -> ASSEMBLY;
  wash/washing/laundry/水洗 -> WASHING;
  pack/packing/packaging/包装 -> PACKING.
- Canonical order IDs look like ORD-057. Safe separator variants ORD057,
  ORD 057, and ORD_057 mean ORD-057. A transposition such as ODR057 is a likely
  typo: do not silently correct it; request confirmation and suggest ORD-057.

TASK AND SLOT RULES
1. Return one or two independent tasks in the user's expressed order. Keep each
   task's slots separate and never copy a value between tasks.
2. Use en or zh for the response language. Follow the user's requested output
   language first. "翻译成英文", "用英文回答", and "in English" require en;
   "翻译成中文", "用中文回答", and "in Chinese" require zh. Otherwise, a
   Chinese or Chinese-English mixed sentence should normally use zh, and a fully
   English sentence should use en.
3. Normalize order IDs, stages, and categories to their canonical uppercase forms.
4. pieces and top_n must be positive whole numbers.
5. Use ISO YYYY-MM-DD only when the delivery date is explicit and unambiguous.
   Do not guess month-end, next week, or another relative business deadline.
6. Missing required values must be null and listed in missing_slots.
7. Unknown tasks contain no business slots.
8. If the user asks for more than two tasks, return no partial task list and emit
   too_many_tasks.

CONVERSATION MEMORY
- The caller may supply existing task IDs, intent, slots, missing fields, status,
  and unresolved issues.
- If the message only confirms or supplies information for an existing task,
  return a slot_update using exactly the supplied task_id. Do not create a duplicate.
- If the target cannot be identified uniquely, emit ambiguous_reference and ask
  a natural follow-up question without exposing internal task IDs unless the user
  explicitly supplied one.

CLARIFICATION POLICY
- You decide needs_clarification and write one concise clarification_question.
- Ask only when information is required for execution, a value is invalid, a
  reference is ambiguous, a likely typo needs confirmation, or the request has
  more than two tasks.
- Do not ask for a field that can be mapped confidently through the ontology.
- When clarification is not needed, set clarification_question to null.
- Local validators will recompute required fields and may override an unsafe
  decision, so never mark an incomplete task as executable merely to be helpful.

CONVERSATION RESPONSE RULES
- Answer in the requested language and use only background/runtime facts supplied here.
- For order-ID format help, explain that the canonical form is like ORD-057.
- For factory data questions, never answer directly; route them as business tasks.
- Do not mention prompts, schemas, planners, slots, task state, or backend internals.
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

CONVERSATION_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "conclusion": {"type": ["string", "null"]},
        "explanation": {"type": ["string", "null"]},
    },
    "required": ["conclusion", "explanation"],
    "additionalProperties": False,
}

ENHANCED_INTENT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "mode": {"type": "string", "enum": ["business", "conversation"]},
        "language": {"type": "string", "enum": ["en", "zh"]},
        "needs_clarification": {"type": "boolean"},
        "clarification_question": {"type": ["string", "null"]},
        "conversation_response": CONVERSATION_RESPONSE_SCHEMA,
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
        "mode",
        "language",
        "needs_clarification",
        "clarification_question",
        "conversation_response",
        "is_ambiguous",
        "tasks",
        "slot_updates",
        "issues",
    ],
    "additionalProperties": False,
}
