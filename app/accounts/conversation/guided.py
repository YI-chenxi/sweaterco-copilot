"""Server-owned deterministic workflow requests, saved through the normal P4 transaction."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import date

from app.agent.contracts import AgentReply
from app.agent.intents import Intent, IntentDecision, ResponseLanguage
from app.agent.turn_service import TurnResult, reset_request

from .errors import ConversationError


@dataclass(frozen=True)
class GuidedRequest:
    workflow: str
    values: dict
    language: str
    title: str
    intent: Intent

    def document(self):
        return {
            "workflow": self.workflow,
            "values": deepcopy(self.values),
            "language": self.language,
        }


def prepare_guided(workflow, values, language):
    """Allow four workflows and the existing reset control; bind every execution field."""
    if type(values) is not dict or type(language) is not str or language not in {"en", "zh"}:
        raise ConversationError("INVALID_ARGUMENT")
    fields = {
        "Order Status": {"order_id"},
        "Production Health": {"stage"},
        "Morning Briefing": {"top_n"},
        "Check Feasibility": {"category", "pieces", "requested_delivery_date"},
        "Start a fresh request": set(),
    }
    if type(workflow) is not str or workflow not in fields or set(values) != fields[workflow]:
        raise ConversationError("INVALID_ARGUMENT")
    values = deepcopy(values)
    zh = language == "zh"
    if workflow == "Start a fresh request":
        title = "开始新的请求" if zh else "Start a fresh request"
        intent = Intent.UNKNOWN
    elif workflow == "Order Status":
        identifier = values["order_id"]
        if type(identifier) is not str or not identifier.strip() or len(identifier) > 100:
            raise ConversationError("INVALID_ARGUMENT")
        values["order_id"] = identifier.strip().upper()
        title = ("订单状态" if zh else "Order status") + " · " + values["order_id"]
        intent = Intent.ORDER_STATUS
    elif workflow == "Production Health":
        if type(values["stage"]) is not str or values["stage"] not in {
            "KNITTING",
            "ASSEMBLY",
            "WASHING",
            "PACKING",
        }:
            raise ConversationError("INVALID_ARGUMENT")
        title = ("生产健康" if zh else "Production health") + " · " + values["stage"]
        intent = Intent.PRODUCTION_NORMALITY
    elif workflow == "Morning Briefing":
        count = values["top_n"]
        if type(count) is not int or not 1 <= count <= 20:
            raise ConversationError("INVALID_ARGUMENT")
        title = f"运营晨报 · 前 {count} 项" if zh else f"Morning briefing · top {count}"
        intent = Intent.MORNING_BRIEFING
    else:
        if type(values["category"]) is not str or values["category"] not in {"TOPS", "ACCESSORIES"}:
            raise ConversationError("INVALID_ARGUMENT")
        count = values["pieces"]
        if type(count) is not int or not 1 <= count <= 100000:
            raise ConversationError("INVALID_ARGUMENT")
        try:
            requested = values["requested_delivery_date"]
            if type(requested) is not str or date.fromisoformat(requested).isoformat() != requested:
                raise ValueError()
        except (TypeError, ValueError):
            raise ConversationError("INVALID_ARGUMENT") from None
        title = (
            f"接单评估 · {count} {values['category']} 交期 {requested}"
            if zh
            else f"Feasibility · {count} {values['category']} by {requested}"
        )
        intent = Intent.ORDER_FEASIBILITY
    return GuidedRequest(workflow, values, language, title, intent)


class GuidedTurnAgent:
    def __init__(self, request, orchestrator):
        self.request = request
        self.orchestrator = orchestrator

    def handle_turn(self, text, session):
        if text != self.request.title:
            raise ConversationError("INVALID_ARGUMENT")
        if self.request.workflow == "Start a fresh request":
            return TurnResult(
                AgentReply(
                    text=self.request.title,
                    metadata={
                        "ui_control": "reset_request",
                        "ui_workflow_request": self.request.document(),
                    },
                ),
                reset_request(session),
            )
        reply = self.orchestrator.handle(
            IntentDecision(
                intent=self.request.intent,
                language=ResponseLanguage(self.request.language),
                slots=deepcopy(self.request.values),
            )
        )
        reply.metadata["ui_workflow_request"] = self.request.document()
        return TurnResult(reply, deepcopy(session))
