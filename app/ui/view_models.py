"""Pure UI adapters for the existing AgentReply contract.

These helpers do not calculate business metrics. They only select values
that have already been computed by the deterministic backend.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Any

from app.agent.contracts import AgentReply
from app.agent.intents import Intent, IntentDecision, ResponseLanguage


@dataclass(frozen=True)
class ResultMetric:
    label: str
    value: str
    detail: str | None = None


def _display(value: Any) -> str:
    if value is None:
        return "—"
    return str(value)


def _assessment(value: Any) -> str:
    labels = {
        "NORMAL": "On track",
        "AT_RISK": "At risk",
        "WATCH": "Needs attention",
        "ABNORMAL": "Outside normal range",
        "SUPPORTED": "Feasible with internal capacity",
        "CONDITIONAL": "Conditionally feasible",
        "NOT_SUPPORTED": "Not currently feasible",
    }
    raw = _display(value)
    return labels.get(raw, raw.replace("_", " ").title())


def metrics_for_reply(reply: AgentReply) -> list[ResultMetric]:
    """Render only backend-provided structured results; never invent values."""
    intent = reply.metadata.get("intent")
    raw = reply.metadata.get("structured_result")
    if not isinstance(raw, dict):
        return []
    if intent == Intent.ORDER_STATUS.value:
        derived = raw.get("derived_metrics", {})
        return [
            ResultMetric("Order", _display(raw.get("order_id"))),
            ResultMetric("Assessment", _assessment(raw.get("judgement"))),
            ResultMetric("Days to due", _display(derived.get("days_to_due"))),
            ResultMetric("Days since activity", _display(derived.get("days_since_activity"))),
        ]
    if intent == Intent.PRODUCTION_NORMALITY.value:
        return [
            ResultMetric("Stage", _display(raw.get("stage"))),
            ResultMetric("Assessment", _assessment(raw.get("judgement"))),
            ResultMetric("Baseline / day", _display(raw.get("historical_baseline"))),
            ResultMetric("Recent / day", _display(raw.get("recent_performance"))),
            ResultMetric("Deviation", f"{_display(raw.get('deviation_pct'))}%"),
        ]
    if intent == Intent.ORDER_FEASIBILITY.value:
        return [
            ResultMetric("Estimate", _assessment(raw.get("estimate"))),
            ResultMetric("Production days", _display(raw.get("working_days"))),
        ]
    if intent == Intent.MORNING_BRIEFING.value:
        findings = raw.get("findings", [])
        return [ResultMetric("Priority findings", str(len(findings)))]
    return []


def findings_for_reply(reply: AgentReply) -> list[dict[str, Any]]:
    """Build a bounded, readable display table from existing briefing findings."""
    raw = reply.metadata.get("structured_result", {})
    findings = raw.get("findings", []) if isinstance(raw, dict) else []
    if not isinstance(findings, list):
        return []
    return [
        {
            "Priority": index,
            "Finding": finding.get("title", finding.get("finding_id", "—")),
            "Severity": finding.get("severity_score"),
            "Type": finding.get("finding_type"),
        }
        for index, finding in enumerate(findings, start=1)
        if isinstance(finding, dict)
    ]


def evidence_rows(reply: AgentReply) -> list[dict[str, str]]:
    """Preserve every source reference, including evidence from multi-row queries."""
    return [
        {"Source": item.source, "Reference": item.reference, "Detail": item.detail}
        for item in reply.evidence
    ]


def evidence_csv(reply: AgentReply) -> str:
    """CSV export for the entire evidence list (the on-page table is capped)."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["Source", "Reference", "Detail"])
    writer.writeheader()
    writer.writerows(evidence_rows(reply))
    return buf.getvalue()


def guided_decision(workflow: str, *, language: str = "en", **values: Any) -> IntentDecision:
    """Create an explicit, user-supplied decision for offline A1/A2 workflows.

    This is *not* natural-language parsing and does not use the LLM.
    All business validation remains in AgentOrchestrator and its tools.
    """
    names = {
        "Order status": Intent.ORDER_STATUS,
        "Production health": Intent.PRODUCTION_NORMALITY,
        "Morning briefing": Intent.MORNING_BRIEFING,
        "Order feasibility": Intent.ORDER_FEASIBILITY,
    }
    if workflow not in names:
        raise ValueError("Unknown guided workflow")
    if language not in {"en", "zh"}:
        raise ValueError("Unsupported language")
    return IntentDecision(
        intent=names[workflow],
        language=ResponseLanguage(language),
        slots=values,
    )
