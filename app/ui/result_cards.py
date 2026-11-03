"""Presentation-only selections from stored results, without business calculations."""

from app.ui.language import translate
from app.ui.localized_reply import localized_result_text
from app.ui.view_models import metrics_for_reply


def card_summary(reply, language, *, text=None):
    raw = reply.metadata.get("structured_result")
    raw = raw if isinstance(raw, dict) else {}
    intent = reply.metadata.get("intent")
    workflow = {
        "order_status": "Order Status",
        "production_normality": "Production Health",
        "morning_briefing": "Morning Briefing",
        "order_feasibility": "Check Feasibility",
    }.get(intent, "Business analysis")
    metrics = [m for m in metrics_for_reply(reply, language=language) if m.value != "—"]
    if intent in {"order_status", "production_normality"}:
        metrics = [m for m in metrics if m.label != translate("Assessment", language)]
    if intent == "production_normality":
        stage_label = translate("Stage", language)
        metrics = [m for m in metrics if m.label != stage_label] + [
            m for m in metrics if m.label == stage_label
        ]
    metrics = [m for m in metrics if m.value != "—%"]
    status = raw.get("judgement", raw.get("estimate"))
    tone = {
        "NORMAL": "normal",
        "SUPPORTED": "normal",
        "AT_RISK": "risk",
        "NOT_SUPPORTED": "risk",
        "ABNORMAL": "watch",
        "WATCH": "watch",
        "CONDITIONAL": "watch",
    }.get(status, "neutral")
    text = text if text is not None else localized_result_text(reply, language) or reply.text
    conclusion = text.strip().split("\n", 1)[0].strip().strip("*# ")
    if len(conclusion) > 240:
        conclusion = "业务分析结果" if language == "zh" else "Business analysis result"
    return (
        workflow,
        str(raw.get("order_id") or raw.get("stage") or raw.get("category") or ""),
        tone,
        conclusion,
        metrics,
    )
