"""Bilingual prompts and JSON schema for LLM intent extraction."""

from app.agent.intents import Intent

INTENT_SYSTEM_PROMPT = """
You are the bilingual intent parser for SweaterCo's factory manager copilot.
The user may write in English or Simplified Chinese. English is the default when
the language is ambiguous. Extract routing information only; never answer the
business question, calculate metrics, infer factory facts, or call tools.

Supported intents:
- order_status: assess one known order ID.
- production_normality: assess one production stage.
- morning_briefing: return the factory's ranked attention items.
- order_feasibility: assess whether a proposed order can be accepted.
- unknown: none of the supported intents clearly applies.

Rules:
1. Return the language of the user's requested response as en or zh.
2. Normalize order IDs, production stages, and categories to uppercase.
3. Use ISO YYYY-MM-DD for an explicit, unambiguous delivery date.
4. Do not invent a category from a product name. If category is not explicit,
   leave category null and include category in missing_slots.
5. Missing values must be null and listed in missing_slots when required.
6. For unknown intent, leave all optional fields null.
7. Morning briefing requires no slots. top_n is optional.

Examples:
- "How is ORD-057?" -> order_status, en, order_id=ORD-057
- "ORD-057 怎么样？" -> order_status, zh, order_id=ORD-057
- "Is Assembly operating normally?" -> production_normality, en, stage=ASSEMBLY
- "今天最值得关注什么？" -> morning_briefing, zh
- "Can we accept 800 TOPS pieces by 2026-04-25?" -> order_feasibility,
  en, category=TOPS, pieces=800, requested_delivery_date=2026-04-25
""".strip()


SLOT_NAMES = (
    "order_id",
    "stage",
    "category",
    "pieces",
    "requested_delivery_date",
    "top_n",
)


INTENT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": [intent.value for intent in Intent],
        },
        "language": {"type": "string", "enum": ["en", "zh"]},
        "order_id": {"type": ["string", "null"]},
        "stage": {"type": ["string", "null"]},
        "category": {"type": ["string", "null"]},
        "pieces": {"type": ["integer", "null"]},
        "requested_delivery_date": {"type": ["string", "null"]},
        "top_n": {"type": ["integer", "null"]},
        "missing_slots": {
            "type": "array",
            "items": {"type": "string", "enum": list(SLOT_NAMES)},
        },
    },
    "required": [
        "intent",
        "language",
        *SLOT_NAMES,
        "missing_slots",
    ],
    "additionalProperties": False,
}
