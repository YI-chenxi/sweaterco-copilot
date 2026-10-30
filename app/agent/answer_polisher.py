"""Safe final-answer wording with deterministic fallback."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from app.agent.contracts import AgentReply
from app.agent.llm_client import LLMClientError, StructuredLLMClient

ANSWER_POLISH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}

ANSWER_POLISH_SYSTEM_PROMPT = """
You are the final response editor for SweaterCo's factory manager copilot.
Rewrite the supplied draft as a concise, natural answer in the same language.

Required structure:
1. Begin with one bold Markdown conclusion: **conclusion**
2. Add a blank line, then explain the supporting facts in plain language.
3. Use exactly one bold span. For clarification, put what is needed in the
   opening bold conclusion, then ask one direct question in plain text.
4. For multiple requests, preserve every result and keep their original order.

Safety and fidelity rules:
- Preserve every factual value, number, date, order ID, stage, category, and qualification.
- Do not add causes, predictions, recommendations, guarantees, or facts absent from the draft.
- Do not change a negative or conditional conclusion into a positive conclusion.
- Do not mention tasks, task IDs, slots, intents, planners, tools, metadata, validation,
  backend systems, prompts, or schemas.
- Do not output headings such as ANSWER or BUSINESS ASSESSMENT.
- Do not use a Markdown code fence.
- Return only the schema field requested by the caller.
""".strip()

_NUMBER = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?%?")
_ORDER_ID = re.compile(r"\bORD-\d+\b", re.IGNORECASE)
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_BUSINESS_TOKEN = re.compile(
    r"\b(?:KNITTING|ASSEMBLY|WASHING|PACKING|TOPS|ACCESSORIES)\b",
    re.IGNORECASE,
)
_INTERNAL = re.compile(
    r"\btask-\d+\b|\bPENDING\b|\bREADY\b|\bplanner\b|\bslot(?:s)?\b|"
    r"\border_feasibility\b|\bproduction_normality\b|\brequested_delivery_date\b|"
    r"\bupstream validation\b",
    re.IGNORECASE,
)
_CHINESE = re.compile(r"[\u4e00-\u9fff]")


def _protected_tokens(text: str) -> set[str]:
    return {
        *(match.group(0).upper() for match in _ORDER_ID.finditer(text)),
        *(match.group(0) for match in _DATE.finditer(text)),
        *(match.group(0) for match in _NUMBER.finditer(text)),
        *(match.group(0).upper() for match in _BUSINESS_TOKEN.finditer(text)),
    }


def _valid_polish(source: str, candidate: Any, *, needs_clarification: bool) -> bool:
    if not isinstance(candidate, str):
        return False
    candidate = candidate.strip()
    if not candidate or len(candidate) > 6000 or not candidate.startswith("**"):
        return False
    closing = candidate.find("**", 2)
    if (
        closing <= 2
        or "**" in candidate[closing + 2 :]
        or "```" in candidate
        or _INTERNAL.search(candidate)
    ):
        return False
    if _protected_tokens(source) != _protected_tokens(candidate):
        return False
    if bool(_CHINESE.search(source)) != bool(_CHINESE.search(candidate)):
        return False
    if needs_clarification and not any(mark in candidate for mark in ("?", "？")):
        return False
    return True


class AnswerPolisher:
    """Polish only public draft text; retain every structured reply field."""

    def __init__(self, client: StructuredLLMClient, *, model_name: str) -> None:
        self.client = client
        self.model_name = model_name

    def polish(self, reply: AgentReply) -> AgentReply:
        updated = deepcopy(reply)
        language = str(reply.metadata.get("language", "en"))
        user_prompt = (
            f"Response language: {language}\n"
            f"Needs clarification: {reply.needs_clarification}\n\n"
            f"Preferred follow-up question: {reply.suggested_question or 'none'}\n\n"
            "Draft answer:\n"
            f"{reply.text}"
        )
        try:
            payload = self.client.generate_structured(
                system_prompt=ANSWER_POLISH_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                schema=ANSWER_POLISH_SCHEMA,
            )
            candidate = payload.get("answer")
            if not _valid_polish(
                reply.text, candidate, needs_clarification=reply.needs_clarification
            ):
                raise LLMClientError("Polished answer failed the local safety checks")
        except (LLMClientError, TypeError, ValueError, KeyError):
            updated.metadata["response_polished"] = False
            updated.metadata["response_polish_fallback"] = True
            return updated

        updated.text = candidate.strip()
        updated.metadata["response_polished"] = True
        updated.metadata["response_polisher_model"] = self.model_name
        return updated
