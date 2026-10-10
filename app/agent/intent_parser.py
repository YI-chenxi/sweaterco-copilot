"""Convert English or Chinese user text into a validated IntentDecision."""

from __future__ import annotations

import logging
from typing import Any

from app.agent.intents import Intent, IntentDecision, ResponseLanguage
from app.agent.llm_client import LLMClientError, StructuredLLMClient
from app.agent.prompts import INTENT_RESPONSE_SCHEMA, INTENT_SYSTEM_PROMPT, SLOT_NAMES

LOGGER = logging.getLogger(__name__)


def detect_response_language(text: str) -> ResponseLanguage:
    """Use a deterministic language fallback when model parsing fails."""

    if any("\u4e00" <= character <= "\u9fff" for character in text):
        return ResponseLanguage.CHINESE
    return ResponseLanguage.ENGLISH


def decision_from_payload(
    payload: dict[str, Any], *, fallback_language: ResponseLanguage
) -> IntentDecision:
    """Validate model output and normalize supported slot values."""

    try:
        intent = Intent(payload.get("intent", Intent.UNKNOWN.value))
    except ValueError:
        intent = Intent.UNKNOWN
    try:
        language = ResponseLanguage(payload.get("language", fallback_language.value))
    except ValueError:
        language = fallback_language

    slots: dict[str, Any] = {}
    for slot in SLOT_NAMES:
        value = payload.get(slot)
        if value is None:
            continue
        if slot in {"order_id", "stage", "category"}:
            cleaned = str(value).strip().upper()
            if cleaned:
                slots[slot] = cleaned
        elif slot == "requested_delivery_date":
            cleaned = str(value).strip()
            if cleaned:
                slots[slot] = cleaned
        elif slot in {"pieces", "top_n"}:
            if isinstance(value, bool):
                continue
            try:
                slots[slot] = int(value)
            except (TypeError, ValueError):
                continue

    declared_missing = payload.get("missing_slots", [])
    if not isinstance(declared_missing, list):
        declared_missing = []
    missing_slots = tuple(
        sorted(
            {
                slot
                for slot in declared_missing
                if isinstance(slot, str) and slot in SLOT_NAMES
            }
        )
    )
    if intent == Intent.UNKNOWN:
        slots = {}
        missing_slots = ()
    return IntentDecision(
        intent=intent,
        slots=slots,
        missing_slots=missing_slots,
        language=language,
    )


class IntentParser:
    """Use a structured-output LLM only for language understanding."""

    def __init__(self, client: StructuredLLMClient) -> None:
        self.client = client

    def parse(self, user_text: str) -> IntentDecision:
        """Parse bilingual text and safely fall back to UNKNOWN on model failure."""

        if not isinstance(user_text, str) or not user_text.strip():
            return IntentDecision(intent=Intent.UNKNOWN)
        fallback_language = detect_response_language(user_text)
        try:
            payload = self.client.generate_structured(
                system_prompt=INTENT_SYSTEM_PROMPT,
                user_prompt=user_text.strip(),
                schema=INTENT_RESPONSE_SCHEMA,
            )
            if not isinstance(payload, dict):
                raise LLMClientError("Structured LLM output must be an object")
            return decision_from_payload(payload, fallback_language=fallback_language)
        except (LLMClientError, TypeError, ValueError) as exc:
            LOGGER.warning("Intent parsing failed; using UNKNOWN fallback: %s", exc)
            return IntentDecision(intent=Intent.UNKNOWN, language=fallback_language)
