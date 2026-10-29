"""Display-only contextual cards from existing structured A2 results.

No invented inventory, ETAs, completion percentages, or quality measurements.
"""

from __future__ import annotations

from typing import Any

from app.agent.contracts import AgentReply
from app.agent.intents import Intent

_ASSESSMENT_LABELS = {
    "NORMAL": "On track",
    "AT_RISK": "At risk",
    "WATCH": "Needs attention",
    "ABNORMAL": "Outside normal range",
    "SUPPORTED": "Feasible with internal capacity",
    "CONDITIONAL": "Conditionally feasible",
    "NOT_SUPPORTED": "Not currently feasible",
}


def _assessment(value: Any) -> str:
    raw = str(value or "Unknown")
    return _ASSESSMENT_LABELS.get(raw, raw.replace("_", " ").title())


def reply_headline(reply: AgentReply) -> tuple[str, str]:
    chinese = reply.metadata.get("language") == "zh"
    if reply.needs_clarification:
        return ("还需要一点信息" if chinese else "One more detail"), "caution"
    if reply.metadata.get("status") in {"failed", "empty"}:
        return ("暂时没有可靠结果" if chinese else "No verified result"), "caution"
    raw = reply.metadata.get("structured_result")
    if not isinstance(raw, dict):
        return ("分析结果" if chinese else "Answer"), "neutral"
    intent = reply.metadata.get("intent")
    if intent == Intent.ORDER_STATUS.value:
        judgement = str(raw.get("judgement", "Unknown"))
        prefix = "订单" if chinese else "Order"
        return (
            f"{prefix} {raw.get('order_id', '')} · {_assessment(judgement)}",
            "caution" if judgement in {"LATE", "AT_RISK"} else "good",
        )
    if intent == Intent.PRODUCTION_NORMALITY.value:
        judgement = str(raw.get("judgement", "Unknown"))
        return (
            f"{raw.get('stage', 'Production')} · {_assessment(judgement)}",
            "caution" if judgement == "ABNORMAL" else "good",
        )
    if intent == Intent.MORNING_BRIEFING.value:
        count = len(raw.get("findings", []))
        return (
            f"晨报 · {count} 项重点" if chinese else f"Morning briefing · {count} priorities"
        ), "neutral"
    if intent == Intent.ORDER_FEASIBILITY.value:
        estimate = str(raw.get("estimate", "Unknown"))
        prefix = "接单评估" if chinese else "Order assessment"
        return (
            f"{prefix} · {_assessment(estimate)}",
            "caution" if estimate != "SUPPORTED" else "good",
        )
    return ("分析结果" if chinese else "Answer"), "neutral"


def contextual_sections(reply: AgentReply) -> list[dict[str, Any]]:
    """Return display specs only for context backed by actual structured data."""
    data = reply.metadata.get("structured_result")
    if not isinstance(data, dict):
        return []
    intent = reply.metadata.get("intent")
    sections = []
    if intent == Intent.ORDER_STATUS.value:
        metrics = data.get("derived_metrics", {})
        facts = [
            ("Order ID", str(data.get("order_id", "—"))),
            ("Business assessment", _assessment(data.get("judgement"))),
        ]
        if "days_to_due" in metrics:
            facts.append(("Days to due (snapshot)", str(metrics["days_to_due"])))
        if "days_since_activity" in metrics:
            facts.append(("Days since activity", str(metrics["days_since_activity"])))
        sections.append(
            {
                "title": "▣ Order details",
                "facts": facts,
                "description": (
                    "Derived order-status facts; no per-order completion percentage is available."
                ),
            }
        )
    elif intent == Intent.PRODUCTION_NORMALITY.value:
        facts = [
            ("Historical baseline", str(data.get("historical_baseline", "—"))),
            ("Recent output", str(data.get("recent_performance", "—"))),
            ("Deviation (%)", str(data.get("deviation_pct", "—"))),
            ("Assessment", _assessment(data.get("judgement"))),
        ]
        sections.append(
            {
                "title": "▥ Historical reference",
                "facts": facts,
                "description": ("Recorded production performance versus the established baseline."),
            }
        )
    elif intent == Intent.ORDER_FEASIBILITY.value:
        margins = data.get("capacity_margin")
        if isinstance(margins, dict):
            sections.append(
                {
                    "title": "▥ Capacity analysis",
                    "description": (
                        "Estimated internal capacity margins from deterministic "
                        "business rules (pieces)."
                    ),
                    "rows": [
                        {"Production stage": k, "Capacity margin": v} for k, v in margins.items()
                    ],
                    "expanded": True,
                }
            )
        options = data.get("external_options")
        if isinstance(options, list) and options:
            columns = (
                "name",
                "delay_days",
                "nominal_capacity",
                "quality_adjusted_capacity",
                "can_cover_new_order_nominally",
            )
            sections.append(
                {
                    "title": "▣ External workshops & lead time",
                    "description": (
                        "Workshop estimates only; not live supplier confirmations "
                        "or materials stock."
                    ),
                    "rows": [
                        {k: opt.get(k) for k in columns} for opt in options if isinstance(opt, dict)
                    ],
                }
            )
    # No material availability, shipment photos, completion %, or live sensor data
    # exists in the provided backend contract. Never create fake UI cards for them.
    return sections
