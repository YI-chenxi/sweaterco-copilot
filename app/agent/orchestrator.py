"""Deterministic orchestration from structured intent to an AgentReply."""

from collections.abc import Callable
from typing import Any

from app.agent.contracts import AgentReply
from app.agent.intents import Intent, IntentDecision, ResponseLanguage, find_missing_slots
from app.agent.response_builder import (
    build_clarification_reply,
    build_failure_reply,
    build_feasibility_reply,
    build_morning_briefing_reply,
    build_order_status_reply,
    build_production_normality_reply,
    build_unknown_intent_reply,
)
from app.agent.settings import AgentSettings
from app.agent.tool_registry import AgentToolRegistry
from app.tools.a2_integration import (
    discover_factory_risks,
    estimate_feasibility_from_retrieval,
    judge_order_from_retrieval,
    judge_production_from_retrieval,
)


class AgentOrchestrator:
    """Select and run existing A1 -> A2 pipelines without using an LLM."""

    def __init__(
        self,
        registry: AgentToolRegistry | None = None,
        settings: AgentSettings | None = None,
    ) -> None:
        self.registry = registry or AgentToolRegistry.from_project_data()
        self.settings = settings or AgentSettings()
        self._handlers: dict[
            Intent, Callable[[dict[str, Any], ResponseLanguage], AgentReply]
        ] = {
            Intent.ORDER_STATUS: self._handle_order_status,
            Intent.PRODUCTION_NORMALITY: self._handle_production_normality,
            Intent.MORNING_BRIEFING: self._handle_morning_briefing,
            Intent.ORDER_FEASIBILITY: self._handle_order_feasibility,
        }

    def handle(self, decision: IntentDecision) -> AgentReply:
        """Validate a routing decision, execute its pipeline, and build a reply."""

        if decision.intent == Intent.UNKNOWN:
            return build_unknown_intent_reply(decision.language)

        missing_slots = find_missing_slots(decision)
        if missing_slots:
            return build_clarification_reply(decision, missing_slots)

        handler = self._handlers.get(decision.intent)
        if handler is None:
            return build_unknown_intent_reply(decision.language)

        try:
            return handler(decision.slots, decision.language)
        except (TypeError, ValueError) as exc:
            return build_failure_reply(decision.intent, str(exc), decision.language)

    def _handle_order_status(
        self, slots: dict[str, Any], language: ResponseLanguage
    ) -> AgentReply:
        retrieval = self.registry.order_lookup.execute(order_id=str(slots["order_id"]))
        result = judge_order_from_retrieval(
            retrieval,
            today=self.settings.reference_date,
        )
        return build_order_status_reply(result, language)

    def _handle_production_normality(
        self, slots: dict[str, Any], language: ResponseLanguage
    ) -> AgentReply:
        stage = str(slots["stage"]).strip().upper()
        retrieval = self.registry.production_retrieval.execute(stage=stage)
        result = judge_production_from_retrieval(retrieval, stage=stage)
        return build_production_normality_reply(result, language)

    def _handle_morning_briefing(
        self, slots: dict[str, Any], language: ResponseLanguage
    ) -> AgentReply:
        top_n = slots.get("top_n", self.settings.briefing_top_n)
        if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n <= 0:
            raise ValueError("top_n must be a positive integer")
        result = discover_factory_risks(
            order_search_tool=self.registry.order_search,
            production_retrieval_tool=self.registry.production_retrieval,
            today=self.settings.reference_date,
            top_n=top_n,
        )
        return build_morning_briefing_reply(result, language)

    def _handle_order_feasibility(
        self, slots: dict[str, Any], language: ResponseLanguage
    ) -> AgentReply:
        pieces = slots["pieces"]
        if isinstance(pieces, bool):
            raise ValueError("pieces must be a positive integer")
        try:
            pieces = int(pieces)
        except (TypeError, ValueError) as exc:
            raise ValueError("pieces must be a positive integer") from exc
        if pieces <= 0:
            raise ValueError("pieces must be a positive integer")
        request = {
            "category": slots["category"],
            "pieces": pieces,
            "requested_delivery_date": slots["requested_delivery_date"],
            "today": self.settings.reference_date,
        }
        result = estimate_feasibility_from_retrieval(
            request=request,
            order_search_tool=self.registry.order_search,
            production_retrieval_tool=self.registry.production_retrieval,
            workshop_retrieval_tool=self.registry.workshop_retrieval,
        )
        return build_feasibility_reply(result, language)
