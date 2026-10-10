"""Deterministic, manager-readable responses for structured tool results."""

from typing import Any

from app.agent.contracts import AgentReply, ToolResult
from app.agent.intents import Intent, IntentDecision

SLOT_LABELS = {
    "category": "产品类别",
    "order_id": "订单号",
    "pieces": "订单件数",
    "requested_delivery_date": "期望交付日期",
    "stage": "生产工序",
}


def _warning_suffix(warnings: list[str]) -> str:
    if not warnings:
        return ""
    return "\n\n提示：" + "；".join(warnings)


def _format_days_to_due(days_to_due: int) -> str:
    if days_to_due < 0:
        return f"交期已过去 {abs(days_to_due)} 天"
    if days_to_due == 0:
        return "今天到期"
    return f"距离交期还有 {days_to_due} 天"


def build_clarification_reply(
    decision: IntentDecision, missing_slots: tuple[str, ...]
) -> AgentReply:
    """Ask for the exact information required by the selected pipeline."""

    labels = [SLOT_LABELS.get(slot, slot) for slot in missing_slots]
    requested = "、".join(labels)
    question = f"请补充以下信息：{requested}。"
    return AgentReply(
        text=question,
        needs_clarification=True,
        suggested_question=question,
        metadata={"intent": decision.intent.value, "missing_slots": list(missing_slots)},
    )


def build_unknown_intent_reply() -> AgentReply:
    """Return a safe clarification when no supported intent was selected."""

    question = "你想查询订单状态、生产正常性、晨报风险，还是评估新订单？"
    return AgentReply(
        text="我暂时无法确定你希望执行哪一种工厂查询。",
        needs_clarification=True,
        suggested_question=question,
        metadata={"intent": Intent.UNKNOWN.value},
    )


def build_failure_reply(intent: Intent, message: str) -> AgentReply:
    """Convert a deterministic validation failure into an explicit response."""

    return AgentReply(
        text=f"当前无法完成该查询：{message}",
        warnings=[message],
        metadata={"intent": intent.value, "status": "failed"},
    )


def build_order_status_reply(result: ToolResult) -> AgentReply:
    """Explain an order judgement without changing its labels or metrics."""

    if not result.data:
        message = result.warnings[0] if result.warnings else "没有找到对应订单。"
        return AgentReply(
            text=message,
            evidence=result.evidence,
            warnings=result.warnings,
            metadata={"intent": Intent.ORDER_STATUS.value, "status": "empty"},
        )

    data = result.data
    metrics = data["derived_metrics"]
    judgement = data["judgement"]
    labels = data.get("labels", [])
    label_text = "、".join(labels) if labels else "无风险标签"
    if judgement == "AT_RISK":
        lead = f"订单 {data['order_id']} 当前被判断为 AT_RISK。"
    else:
        lead = f"订单 {data['order_id']} 当前被判断为 NORMAL。"
    text = (
        f"{lead} {_format_days_to_due(metrics['days_to_due'])}，"
        f"距离最近活动 {metrics['days_since_activity']} 天；标签：{label_text}。"
        f"{_warning_suffix(result.warnings)}"
    )
    return AgentReply(
        text=text,
        evidence=result.evidence,
        warnings=result.warnings,
        metadata={
            "intent": Intent.ORDER_STATUS.value,
            "rule_version": data.get("rule_version"),
            "structured_result": data,
        },
    )


def build_production_normality_reply(result: ToolResult) -> AgentReply:
    """Explain a production judgement using only calculated A2 facts."""

    if not result.data:
        message = result.warnings[0] if result.warnings else "没有可用生产记录。"
        return AgentReply(
            text=message,
            evidence=result.evidence,
            warnings=result.warnings,
            metadata={"intent": Intent.PRODUCTION_NORMALITY.value, "status": "empty"},
        )

    data = result.data
    text = (
        f"{data['stage']} 当前被判断为 {data['judgement']}。"
        f"历史基准为 {data['historical_baseline']} 件/生产日，"
        f"最近表现为 {data['recent_performance']} 件/生产日，"
        f"偏差 {data['deviation_pct']}%，方向为 {data['direction']}。"
        f"{_warning_suffix(result.warnings)}"
    )
    return AgentReply(
        text=text,
        evidence=result.evidence,
        warnings=result.warnings,
        metadata={
            "intent": Intent.PRODUCTION_NORMALITY.value,
            "rule_version": data.get("rule_version"),
            "structured_result": data,
        },
    )


def _finding_line(index: int, finding: dict[str, Any]) -> str:
    evidence = finding.get("evidence", {})
    if finding.get("finding_type") == "ORDER_RISK":
        due_text = _format_days_to_due(int(evidence.get("days_to_due", 0)))
        detail = (
            f"{due_text}，距最近活动 {evidence.get('days_since_activity')} 天"
        )
    else:
        detail = f"产出偏差 {evidence.get('deviation_pct')}%"
    return (
        f"{index}. {finding['title']}：风险分 {finding['severity_score']}，{detail}。"
    )


def build_morning_briefing_reply(result: ToolResult) -> AgentReply:
    """Render ranked findings without adding unsupported root-cause claims."""

    findings = result.data.get("findings", []) if result.data else []
    if not findings:
        text = "当前没有发现需要列入晨报的风险事项。"
    else:
        lines = [_finding_line(index, finding) for index, finding in enumerate(findings, 1)]
        text = "当前最值得关注的事项：\n" + "\n".join(lines)
    text += _warning_suffix(result.warnings)
    return AgentReply(
        text=text,
        evidence=result.evidence,
        warnings=result.warnings,
        metadata={
            "intent": Intent.MORNING_BRIEFING.value,
            "rule_version": findings[0].get("rule_version") if findings else None,
            "structured_result": result.data,
        },
    )


def build_feasibility_reply(result: ToolResult) -> AgentReply:
    """Explain feasibility while preserving its conditional nature and assumptions."""

    if not result.data:
        message = result.warnings[0] if result.warnings else "没有足够数据评估接单可行性。"
        return AgentReply(
            text=message,
            evidence=result.evidence,
            warnings=result.warnings,
            metadata={"intent": Intent.ORDER_FEASIBILITY.value, "status": "empty"},
        )

    data = result.data
    negative_margins = {
        stage: margin for stage, margin in data["capacity_margin"].items() if margin < 0
    }
    if negative_margins:
        margin_text = "、".join(
            f"{stage} {margin}" for stage, margin in negative_margins.items()
        )
        capacity_sentence = f"内部产能不足的工序及余量为：{margin_text}。"
    else:
        capacity_sentence = "所有内部工序的保守产能余量均为非负。"

    external_cover = [
        option
        for option in data.get("external_options", [])
        if option.get("can_cover_new_order_nominally")
    ]
    external_sentence = f"共有 {len(external_cover)} 个外协选项具备名义覆盖能力。"
    assumptions = list(data.get("assumptions", []))
    assumption_text = "；".join(assumptions)
    text = (
        f"接单评估结果为 {data['estimate']}，可用生产日为 {data['working_days']} 天。"
        f"{capacity_sentence}{external_sentence}"
        f"本结果基于以下假设：{assumption_text}。"
        f"{_warning_suffix(result.warnings)}"
    )
    return AgentReply(
        text=text,
        evidence=result.evidence,
        warnings=result.warnings,
        assumptions=assumptions,
        metadata={
            "intent": Intent.ORDER_FEASIBILITY.value,
            "rule_version": data.get("rule_version"),
            "structured_result": data,
        },
    )
