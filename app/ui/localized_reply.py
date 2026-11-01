"""Optional locale views of stored results; originals are always retained.

Reuse the existing deterministic bilingual formatters, not tools or model calls.
The generated view is a data interpretation, not a translation of arbitrary prose.
"""

from copy import deepcopy

from app.agent.contracts import AgentReply, ToolResult
from app.agent.intents import ResponseLanguage
from app.agent.response_builder import (
    build_feasibility_reply,
    build_morning_briefing_reply,
    build_order_status_reply,
    build_production_normality_reply,
)


def localized_result_text(reply: AgentReply, language: str) -> str | None:
    if language not in {"en", "zh"} or reply.metadata.get("language") == language:
        return None
    if reply.needs_clarification or reply.metadata.get("status") in {"failed", "empty"}:
        return None
    data = reply.metadata.get("structured_result")
    builders = {
        "order_status": build_order_status_reply,
        "production_normality": build_production_normality_reply,
        "morning_briefing": build_morning_briefing_reply,
        "order_feasibility": build_feasibility_reply,
    }
    builder = builders.get(reply.metadata.get("intent"))
    if not builder or not isinstance(data, dict) or not data:
        return None
    source = ToolResult(
        data=deepcopy(data), evidence=deepcopy(reply.evidence), warnings=list(reply.warnings)
    )
    try:
        return builder(source, ResponseLanguage(language)).text
    except (KeyError, TypeError, ValueError):
        # Incomplete historical results must never be completed using guessed values.
        return None


def display_origin(origin: str, language: str) -> str:
    from app.ui.language import translate

    canonical = {
        "快捷工作流 · 业务规则工具": "Structured workflow · deterministic tools",
        "AI 聊天 · 上下文任务": "AI Chat · contextual tasks",
    }.get(origin, origin)
    return translate(canonical, language)


def localized_assumptions(items: list[str], language: str) -> list[str]:
    """Translate only the existing, reviewed assumption vocabulary."""
    from app.agent.response_builder import ASSUMPTION_TRANSLATIONS

    translations = (
        ASSUMPTION_TRANSLATIONS
        if language == "en"
        else {english: chinese for chinese, english in ASSUMPTION_TRANSLATIONS.items()}
    )
    return [translations.get(item, item) for item in items]
