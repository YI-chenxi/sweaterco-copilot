"""Deterministic bilingual responses for structured tool results."""

from typing import Any

from app.agent.contracts import AgentReply, ToolResult
from app.agent.intents import Intent, IntentDecision, ResponseLanguage

SLOT_LABELS = {
    ResponseLanguage.ENGLISH: {
        "category": "product category",
        "order_id": "order ID",
        "pieces": "order quantity",
        "requested_delivery_date": "requested delivery date",
        "stage": "production stage",
    },
    ResponseLanguage.CHINESE: {
        "category": "产品类别",
        "order_id": "订单号",
        "pieces": "订单件数",
        "requested_delivery_date": "期望交付日期",
        "stage": "生产工序",
    },
}


ASSUMPTION_TRANSLATIONS = {
    "Sunday（周日）不计生产日": "Sundays are excluded from production days",
    "Planning Rate（规划速率）取历史基准与最近 6 个生产日均值的较低者": (
        "The planning rate uses the lower of the historical baseline and the "
        "most recent six production-day average"
    ),
    "Existing Load Proxy（现有负载代理）假设当前工序订单全部件数仍需经过当前及后续工序": (
        "The existing-load proxy assumes every piece at an order's current stage "
        "must still pass through that stage and all downstream stages"
    ),
    "Quality-adjusted Capacity（质量调整产能）仅为缺陷率情景估算，不是原始事实": (
        "Quality-adjusted capacity is a defect-rate scenario estimate, not a source fact"
    ),
}

SOURCE_MESSAGE_TRANSLATIONS = {
    "request 缺少有效 category": "The request requires a valid category",
    "日期必须是 date 或 YYYY-MM-DD": "The date must be a date or YYYY-MM-DD",
    "pieces 必须大于 0": "pieces must be greater than 0",
    "requested_delivery_date 必须晚于 today": (
        "requested_delivery_date must be later than today"
    ),
    "top_n 必须大于 0": "top_n must be greater than 0",
}


def _localized_source_message(message: str, language: ResponseLanguage) -> str:
    """Translate known A2 validation text for display while preserving its source."""

    if language == ResponseLanguage.CHINESE:
        return message
    translated = SOURCE_MESSAGE_TRANSLATIONS.get(message)
    if translated:
        return translated
    if message.startswith("request 缺少字段: "):
        return "The request is missing field(s): " + message.removeprefix(
            "request 缺少字段: "
        )
    if message.endswith(" 历史生产数据不足"):
        stage = message.removesuffix(" 历史生产数据不足")
        return f"{stage} has insufficient historical production data"
    return message


def _warning_suffix(warnings: list[str], language: ResponseLanguage) -> str:
    if not warnings:
        return ""
    if language == ResponseLanguage.CHINESE:
        return "\n\n提示：" + "；".join(warnings)
    localized = [_localized_source_message(message, language) for message in warnings]
    return "\n\nWarnings: " + "; ".join(localized)


def _format_days_to_due(days_to_due: int, language: ResponseLanguage) -> str:
    if language == ResponseLanguage.CHINESE:
        if days_to_due < 0:
            return f"交期已过去 {abs(days_to_due)} 天"
        if days_to_due == 0:
            return "今天到期"
        return f"距离交期还有 {days_to_due} 天"
    if days_to_due < 0:
        return f"the due date passed {abs(days_to_due)} days ago"
    if days_to_due == 0:
        return "the order is due today"
    if days_to_due == 1:
        return "the order is due in 1 day"
    return f"the order is due in {days_to_due} days"


def _format_days_since_activity(days: int, language: ResponseLanguage) -> str:
    if language == ResponseLanguage.CHINESE:
        if days == 0:
            return "今天有活动记录"
        return f"距离最近活动 {days} 天"
    if days == 0:
        return "the last recorded activity was today"
    if days == 1:
        return "the last recorded activity was 1 day ago"
    return f"the last recorded activity was {days} days ago"


def build_clarification_reply(
    decision: IntentDecision, missing_slots: tuple[str, ...]
) -> AgentReply:
    """Ask for the exact information required by the selected pipeline."""

    language = decision.language
    labels = [SLOT_LABELS[language].get(slot, slot) for slot in missing_slots]
    if language == ResponseLanguage.CHINESE:
        question = f"请补充以下信息：{'、'.join(labels)}。"
    else:
        question = f"Please provide the following information: {', '.join(labels)}."
    return AgentReply(
        text=question,
        needs_clarification=True,
        suggested_question=question,
        metadata={
            "intent": decision.intent.value,
            "language": language.value,
            "missing_slots": list(missing_slots),
        },
    )


def build_unknown_intent_reply(
    language: ResponseLanguage = ResponseLanguage.ENGLISH,
) -> AgentReply:
    """Return a safe bilingual clarification for an unsupported intent."""

    if language == ResponseLanguage.CHINESE:
        text = "我暂时无法确定你希望执行哪一种工厂查询。"
        question = "你想查询订单状态、生产正常性、晨报风险，还是评估新订单？"
    else:
        text = "I could not determine which supported factory query you need."
        question = (
            "Would you like to check an order, assess a production stage, "
            "view the morning briefing, or evaluate a new order?"
        )
    return AgentReply(
        text=text,
        needs_clarification=True,
        suggested_question=question,
        metadata={"intent": Intent.UNKNOWN.value, "language": language.value},
    )


def build_failure_reply(
    intent: Intent,
    message: str,
    language: ResponseLanguage = ResponseLanguage.ENGLISH,
) -> AgentReply:
    """Convert a deterministic validation failure into an explicit response."""

    prefix = (
        "当前无法完成该查询："
        if language == ResponseLanguage.CHINESE
        else "This request could not be completed: "
    )
    display_message = _localized_source_message(message, language)
    return AgentReply(
        text=f"{prefix}{display_message}",
        warnings=[message],
        metadata={
            "intent": intent.value,
            "language": language.value,
            "status": "failed",
        },
    )


def build_order_status_reply(
    result: ToolResult,
    language: ResponseLanguage = ResponseLanguage.ENGLISH,
) -> AgentReply:
    """Explain an order judgement without changing its labels or metrics."""

    if not result.data:
        if result.warnings:
            message = _localized_source_message(result.warnings[0], language)
        elif language == ResponseLanguage.CHINESE:
            message = "没有找到对应订单。"
        else:
            message = "No matching order was found."
        return AgentReply(
            text=message,
            evidence=result.evidence,
            warnings=result.warnings,
            metadata={
                "intent": Intent.ORDER_STATUS.value,
                "language": language.value,
                "status": "empty",
            },
        )

    data = result.data
    metrics = data["derived_metrics"]
    judgement = data["judgement"]
    labels = data.get("labels", [])
    if language == ResponseLanguage.CHINESE:
        label_text = "、".join(labels) if labels else "无风险标签"
        text = (
            f"订单 {data['order_id']} 当前被判断为 {judgement}。"
            f"{_format_days_to_due(metrics['days_to_due'], language)}，"
            f"{_format_days_since_activity(metrics['days_since_activity'], language)}；"
            f"标签：{label_text}。"
        )
    else:
        label_text = ", ".join(labels) if labels else "none"
        due_text = _format_days_to_due(metrics["days_to_due"], language)
        due_text = due_text[0].upper() + due_text[1:]
        text = (
            f"Order {data['order_id']} is assessed as {judgement}. "
            f"{due_text}, and "
            f"{_format_days_since_activity(metrics['days_since_activity'], language)}. "
            f"Labels: {label_text}."
        )
    text += _warning_suffix(result.warnings, language)
    return AgentReply(
        text=text,
        evidence=result.evidence,
        warnings=result.warnings,
        metadata={
            "intent": Intent.ORDER_STATUS.value,
            "language": language.value,
            "rule_version": data.get("rule_version"),
            "structured_result": data,
        },
    )


def build_production_normality_reply(
    result: ToolResult,
    language: ResponseLanguage = ResponseLanguage.ENGLISH,
) -> AgentReply:
    """Explain a production judgement using only calculated A2 facts."""

    if not result.data:
        if result.warnings:
            message = _localized_source_message(result.warnings[0], language)
        elif language == ResponseLanguage.CHINESE:
            message = "没有可用生产记录。"
        else:
            message = "No usable production records were found."
        return AgentReply(
            text=message,
            evidence=result.evidence,
            warnings=result.warnings,
            metadata={
                "intent": Intent.PRODUCTION_NORMALITY.value,
                "language": language.value,
                "status": "empty",
            },
        )

    data = result.data
    if language == ResponseLanguage.CHINESE:
        text = (
            f"{data['stage']} 当前被判断为 {data['judgement']}。"
            f"历史基准为 {data['historical_baseline']} 件/生产日，"
            f"最近表现为 {data['recent_performance']} 件/生产日，"
            f"偏差 {data['deviation_pct']}%，方向为 {data['direction']}。"
        )
    else:
        text = (
            f"{data['stage']} is assessed as {data['judgement']}. "
            f"The historical baseline is {data['historical_baseline']} pieces per "
            f"production day, versus {data['recent_performance']} recently, for a "
            f"{data['deviation_pct']}% deviation ({data['direction']})."
        )
    text += _warning_suffix(result.warnings, language)
    return AgentReply(
        text=text,
        evidence=result.evidence,
        warnings=result.warnings,
        metadata={
            "intent": Intent.PRODUCTION_NORMALITY.value,
            "language": language.value,
            "rule_version": data.get("rule_version"),
            "structured_result": data,
        },
    )


def _finding_line(
    index: int, finding: dict[str, Any], language: ResponseLanguage
) -> str:
    evidence = finding.get("evidence", {})
    if finding.get("finding_type") == "ORDER_RISK":
        due_text = _format_days_to_due(int(evidence.get("days_to_due", 0)), language)
        if language == ResponseLanguage.CHINESE:
            title = finding["title"]
            detail = (
                f"{due_text}，"
                f"{_format_days_since_activity(evidence.get('days_since_activity'), language)}"
            )
        else:
            order_id = str(finding.get("finding_id", "")).removeprefix("ORDER:")
            title = f"Order {order_id} risk"
            detail = (
                f"{due_text}; "
                f"{_format_days_since_activity(evidence.get('days_since_activity'), language)}"
            )
    else:
        if language == ResponseLanguage.CHINESE:
            title = finding["title"]
            detail = f"产出偏差 {evidence.get('deviation_pct')}%"
        else:
            stage = str(finding.get("finding_id", "")).removeprefix("STAGE:")
            title = f"{stage} production anomaly"
            detail = f"output deviation {evidence.get('deviation_pct')}%"
    if language == ResponseLanguage.CHINESE:
        return f"{index}. {title}：风险分 {finding['severity_score']}，{detail}。"
    return f"{index}. {title}: severity {finding['severity_score']}; {detail}."


def build_morning_briefing_reply(
    result: ToolResult,
    language: ResponseLanguage = ResponseLanguage.ENGLISH,
) -> AgentReply:
    """Render ranked findings without adding unsupported root-cause claims."""

    findings = result.data.get("findings", []) if result.data else []
    if not findings:
        text = (
            "当前没有发现需要列入晨报的风险事项。"
            if language == ResponseLanguage.CHINESE
            else "No items currently meet the morning briefing risk criteria."
        )
    else:
        lines = [
            _finding_line(index, finding, language)
            for index, finding in enumerate(findings, 1)
        ]
        heading = (
            "当前最值得关注的事项：\n"
            if language == ResponseLanguage.CHINESE
            else "Current priority items:\n"
        )
        text = heading + "\n".join(lines)
    text += _warning_suffix(result.warnings, language)
    return AgentReply(
        text=text,
        evidence=result.evidence,
        warnings=result.warnings,
        metadata={
            "intent": Intent.MORNING_BRIEFING.value,
            "language": language.value,
            "rule_version": findings[0].get("rule_version") if findings else None,
            "structured_result": result.data,
        },
    )


def _localized_assumptions(
    assumptions: list[str], language: ResponseLanguage
) -> list[str]:
    if language == ResponseLanguage.CHINESE:
        return assumptions
    return [ASSUMPTION_TRANSLATIONS.get(item, item) for item in assumptions]


def build_feasibility_reply(
    result: ToolResult,
    language: ResponseLanguage = ResponseLanguage.ENGLISH,
) -> AgentReply:
    """Explain feasibility while preserving its conditional nature and assumptions."""

    if not result.data:
        if result.warnings:
            message = _localized_source_message(result.warnings[0], language)
        elif language == ResponseLanguage.CHINESE:
            message = "没有足够数据评估接单可行性。"
        else:
            message = "There is not enough data to assess order feasibility."
        return AgentReply(
            text=message,
            evidence=result.evidence,
            warnings=result.warnings,
            metadata={
                "intent": Intent.ORDER_FEASIBILITY.value,
                "language": language.value,
                "status": "empty",
            },
        )

    data = result.data
    negative_margins = {
        stage: margin for stage, margin in data["capacity_margin"].items() if margin < 0
    }
    external_cover = [
        option
        for option in data.get("external_options", [])
        if option.get("can_cover_new_order_nominally")
    ]
    source_assumptions = list(data.get("assumptions", []))
    assumptions = _localized_assumptions(source_assumptions, language)

    if language == ResponseLanguage.CHINESE:
        if negative_margins:
            margin_text = "、".join(
                f"{stage} {margin}" for stage, margin in negative_margins.items()
            )
            capacity_sentence = f"内部产能不足的工序及余量为：{margin_text}。"
        else:
            capacity_sentence = "所有内部工序的保守产能余量均为非负。"
        text = (
            f"接单评估结果为 {data['estimate']}，可用生产日为 "
            f"{data['working_days']} 天。{capacity_sentence}"
            f"共有 {len(external_cover)} 个外协选项具备名义覆盖能力。"
            f"本结果基于以下假设：{'；'.join(assumptions)}。"
        )
    else:
        if negative_margins:
            margin_text = ", ".join(
                f"{stage} {margin}" for stage, margin in negative_margins.items()
            )
            capacity_sentence = f"Negative internal margins: {margin_text}. "
        else:
            capacity_sentence = "All conservative internal capacity margins are non-negative. "
        text = (
            f"The feasibility estimate is {data['estimate']} with "
            f"{data['working_days']} available production days. {capacity_sentence}"
            f"{len(external_cover)} external options have enough nominal capacity. "
            f"Assumptions: {'; '.join(assumptions)}."
        )
    text += _warning_suffix(result.warnings, language)
    return AgentReply(
        text=text,
        evidence=result.evidence,
        warnings=result.warnings,
        assumptions=assumptions,
        metadata={
            "intent": Intent.ORDER_FEASIBILITY.value,
            "language": language.value,
            "rule_version": data.get("rule_version"),
            "structured_result": data,
            "source_assumptions": source_assumptions,
        },
    )
