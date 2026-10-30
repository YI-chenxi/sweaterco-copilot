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
    "pieces 必须大于 0": "The order quantity must be greater than zero",
    "requested_delivery_date 必须晚于 today": "The delivery date must be later than today",
    "top_n 必须大于 0": "The number of briefing items must be greater than zero",
}

ORDER_JUDGEMENT_TEXT = {
    ResponseLanguage.ENGLISH: {
        "NORMAL": "does not currently show a delivery risk",
        "AT_RISK": "currently shows a delivery risk",
    },
    ResponseLanguage.CHINESE: {
        "NORMAL": "目前未显示交付风险",
        "AT_RISK": "目前存在交付风险",
    },
}

PRODUCTION_JUDGEMENT_TEXT = {
    ResponseLanguage.ENGLISH: {
        "NORMAL": "is operating within its normal range",
        "WATCH": "is showing a noticeable change and should be watched",
        "ABNORMAL": "is performing outside its normal range",
    },
    ResponseLanguage.CHINESE: {
        "NORMAL": "目前处于正常波动范围",
        "WATCH": "目前出现明显波动，建议持续关注",
        "ABNORMAL": "目前偏离正常生产水平",
    },
}

ORDER_LABEL_TEXT = {
    ResponseLanguage.ENGLISH: {
        "OVERDUE": "overdue",
        "DUE_SOON": "due soon",
        "STALLED": "no recent activity",
        "AT_RISK": "delivery risk",
    },
    ResponseLanguage.CHINESE: {
        "OVERDUE": "已逾期",
        "DUE_SOON": "临近交期",
        "STALLED": "近期无活动",
        "AT_RISK": "存在交付风险",
    },
}

FEASIBILITY_TEXT = {
    ResponseLanguage.ENGLISH: {
        "SUPPORTED": "The order appears feasible with internal capacity.",
        "CONDITIONAL": "The order may be feasible, but it would depend on external capacity.",
        "NOT_SUPPORTED": (
            "The order does not appear feasible under the current capacity assumptions."
        ),
    },
    ResponseLanguage.CHINESE: {
        "SUPPORTED": "按当前产能估算，这笔订单可以由内部产能支持。",
        "CONDITIONAL": "这笔订单有条件可行，但需要依赖外协产能。",
        "NOT_SUPPORTED": "按当前产能和假设，这笔订单暂不具备可行性。",
    },
}


def _localized_source_message(message: str, language: ResponseLanguage) -> str:
    """Translate known A2 validation text for display while preserving its source."""

    if language == ResponseLanguage.CHINESE:
        return message
    translated = SOURCE_MESSAGE_TRANSLATIONS.get(message)
    if translated:
        return translated
    if message.startswith("request 缺少字段: "):
        return "The request is missing field(s): " + message.removeprefix("request 缺少字段: ")
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


def _answer(conclusion: str, explanation: str = "") -> str:
    """Use one consistent conversational structure for every public reply."""

    headline = f"**{conclusion.strip()}**"
    return f"{headline}\n\n{explanation.strip()}" if explanation.strip() else headline


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
    slots = decision.slots
    if decision.intent == Intent.ORDER_STATUS and "order_id" in missing_slots:
        question = (
            "你想查询哪个订单？"
            if language == ResponseLanguage.CHINESE
            else "Which order would you like me to check?"
        )
    elif decision.intent == Intent.PRODUCTION_NORMALITY and "stage" in missing_slots:
        question = (
            "你想检查哪个生产工序？"
            if language == ResponseLanguage.CHINESE
            else "Which production stage would you like me to check?"
        )
    elif decision.intent == Intent.ORDER_FEASIBILITY and missing_slots == (
        "requested_delivery_date",
    ):
        if language == ResponseLanguage.CHINESE:
            details = " ".join(
                value
                for value in (
                    f"{slots.get('pieces')} 件" if slots.get("pieces") else "",
                    str(slots.get("category")) if slots.get("category") else "",
                )
                if value
            )
            question = f"这笔 {details} 订单的期望交付日期是什么时候？"
        else:
            details = " ".join(
                value
                for value in (
                    f"{slots.get('pieces')}-piece" if slots.get("pieces") else "",
                    str(slots.get("category")) if slots.get("category") else "",
                )
                if value
            )
            question = f"What delivery date should I use for the {details} order?"
    elif language == ResponseLanguage.CHINESE:
        question = f"为了继续处理，请告诉我{'、'.join(labels)}。"
    else:
        question = f"To continue, could you provide the {', '.join(labels)}?"
    conclusion = (
        "继续处理前，我还需要一点信息。"
        if language == ResponseLanguage.CHINESE
        else "I need one more detail before I can continue."
    )
    return AgentReply(
        text=_answer(conclusion, question),
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
        conclusion = "我还不确定你想了解哪一项。"
        explanation = "我可以帮你查询订单、检查生产情况、查看晨报，或评估新增订单。"
        question = "你现在想先了解哪一项？"
    else:
        conclusion = "I'm not sure which area you want to look into yet."
        explanation = (
            "I can help with orders, production checks, briefings, and new-order assessments."
        )
        question = "What would you like to look into?"
    return AgentReply(
        text=_answer(conclusion, f"{explanation} {question}"),
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

    conclusion = (
        "抱歉，我暂时无法完成这个请求。"
        if language == ResponseLanguage.CHINESE
        else "Sorry, I couldn't complete that request."
    )
    display_message = _localized_source_message(message, language)
    return AgentReply(
        text=_answer(conclusion, display_message),
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
            text=_answer(message),
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
        label_text = "、".join(ORDER_LABEL_TEXT[language].get(label, label) for label in labels)
        flag_sentence = f"需要关注：{label_text}。" if label_text else "目前没有额外风险提示。"
        conclusion = (
            f"订单 {data['order_id']} {ORDER_JUDGEMENT_TEXT[language].get(judgement, judgement)}。"
        )
        explanation = (
            f"{_format_days_to_due(metrics['days_to_due'], language)}，"
            f"{_format_days_since_activity(metrics['days_since_activity'], language)}；"
            f"{flag_sentence}"
        )
    else:
        label_text = ", ".join(
            ORDER_LABEL_TEXT[language].get(label, label.lower()) for label in labels
        )
        due_text = _format_days_to_due(metrics["days_to_due"], language)
        due_text = due_text[0].upper() + due_text[1:]
        flag_sentence = (
            f"Key concerns: {label_text}." if label_text else "No additional risk flags were found."
        )
        conclusion = (
            f"Order {data['order_id']} {ORDER_JUDGEMENT_TEXT[language].get(judgement, judgement)}."
        )
        explanation = (
            f"{due_text}, and "
            f"{_format_days_since_activity(metrics['days_since_activity'], language)}. "
            f"{flag_sentence}"
        )
    text = _answer(conclusion, explanation) + _warning_suffix(result.warnings, language)
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
            text=_answer(message),
            evidence=result.evidence,
            warnings=result.warnings,
            metadata={
                "intent": Intent.PRODUCTION_NORMALITY.value,
                "language": language.value,
                "status": "empty",
            },
        )

    data = result.data
    assessment = PRODUCTION_JUDGEMENT_TEXT[language].get(data["judgement"], data["judgement"])
    if language == ResponseLanguage.CHINESE:
        conclusion = f"{data['stage']} 工序{assessment}。"
        explanation = (
            f"历史基准为 {data['historical_baseline']} 件/生产日，"
            f"最近表现为 {data['recent_performance']} 件/生产日，"
            f"较基准偏差 {data['deviation_pct']}%。"
        )
    else:
        conclusion = f"{data['stage']} {assessment}."
        explanation = (
            f"The historical baseline is {data['historical_baseline']} pieces per "
            f"production day, versus {data['recent_performance']} recently, for a "
            f"{data['deviation_pct']}% difference."
        )
    text = _answer(conclusion, explanation) + _warning_suffix(result.warnings, language)
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


def _finding_line(index: int, finding: dict[str, Any], language: ResponseLanguage) -> str:
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
        conclusion = (
            "当前没有发现需要列入晨报的风险事项。"
            if language == ResponseLanguage.CHINESE
            else "No items currently meet the morning briefing risk criteria."
        )
        text = _answer(conclusion)
    else:
        lines = [
            _finding_line(index, finding, language) for index, finding in enumerate(findings, 1)
        ]
        conclusion = (
            f"晨报中有 {len(findings)} 项需要优先关注。"
            if language == ResponseLanguage.CHINESE
            else f"There are {len(findings)} priority items in the morning briefing."
        )
        explanation = (
            "具体情况如下：\n"
            if language == ResponseLanguage.CHINESE
            else "Here are the details:\n"
        ) + "\n".join(lines)
        text = _answer(conclusion, explanation)
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


def _localized_assumptions(assumptions: list[str], language: ResponseLanguage) -> list[str]:
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
            text=_answer(message),
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
            stage_text = "、".join(negative_margins)
            capacity_sentence = f"内部产能较紧张的工序包括：{stage_text}。"
        else:
            capacity_sentence = "按保守估算，内部各工序都有足够产能余量。"
        conclusion = FEASIBILITY_TEXT[language].get(data["estimate"], data["estimate"])
        explanation = (
            f"距离交付共有 {data['working_days']} 个可用生产日。{capacity_sentence}"
            f"目前有 {len(external_cover)} 个外协选项在名义产能上可以覆盖这笔订单。"
            "这是一项产能估算，不代表交付承诺；详细假设可在右侧查看。"
        )
    else:
        if negative_margins:
            stages = [stage.replace("_", " ").title() for stage in negative_margins]
            stage_text = (
                stages[0] if len(stages) == 1 else ", ".join(stages[:-1]) + f", and {stages[-1]}"
            )
            capacity_sentence = f"Internal capacity appears tight in {stage_text}. "
        else:
            capacity_sentence = (
                "Each internal stage has capacity available under the conservative estimate. "
            )
        conclusion = FEASIBILITY_TEXT[language].get(data["estimate"], data["estimate"])
        explanation = (
            f"There are {data['working_days']} available production days. {capacity_sentence}"
            f"{len(external_cover)} external options appear to have enough nominal capacity. "
            "This is a capacity estimate, not a delivery commitment; the detailed assumptions "
            "remain available in the analysis panel."
        )
    text = _answer(conclusion, explanation) + _warning_suffix(result.warnings, language)
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
