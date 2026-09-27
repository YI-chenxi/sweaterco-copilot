"""订单状态判断核心业务函数。

设计原则：
1. 只基于调用方传入的订单事实做确定性判断；
2. 不预测完工日期，不推断停滞原因；
3. 返回派生指标、原因代码和规则版本，支持追溯。
"""

from datetime import date, datetime
from typing import Any

from .config import ALLOWED_STATUSES, DUE_SOON_DAYS, RULE_VERSION, STALLED_DAYS
from .models import OrderStatusInput, OrderStatusResult


def _parse_date(value: date | str, field_name: str) -> date:
    """把 ISO 日期字符串标准化为 date；非法输入直接报错，避免静默误判。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{field_name} 必须是 YYYY-MM-DD 格式，收到: {value!r}") from exc
    raise TypeError(f"{field_name} 必须是 date 或 YYYY-MM-DD 字符串")


def _build_input(payload: dict[str, Any] | OrderStatusInput) -> OrderStatusInput:
    """校验并标准化业务输入。"""
    if isinstance(payload, OrderStatusInput):
        return payload
    if not isinstance(payload, dict):
        raise TypeError("payload 必须是 dict 或 OrderStatusInput")

    required = {
        "order_id",
        "pieces",
        "due_date",
        "status",
        "current_stage",
        "last_activity_date",
        "today",
    }
    missing = sorted(required - payload.keys())
    if missing:
        raise ValueError(f"缺少必填字段: {', '.join(missing)}")

    order_id = str(payload["order_id"]).strip()
    if not order_id:
        raise ValueError("order_id 不能为空")

    pieces = payload["pieces"]
    # bool 在 Python 中属于 int 的子类，因此显式排除，避免 True 被当成 1 件。
    if isinstance(pieces, bool) or not isinstance(pieces, int) or pieces <= 0:
        raise ValueError("pieces 必须是大于 0 的整数")

    status = str(payload["status"]).strip().upper()
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"status 仅支持 {sorted(ALLOWED_STATUSES)}，收到: {status!r}")

    current_stage = str(payload["current_stage"]).strip().upper()
    if not current_stage:
        raise ValueError("current_stage 不能为空")

    due_date = _parse_date(payload["due_date"], "due_date")
    last_activity_date = _parse_date(payload["last_activity_date"], "last_activity_date")
    today = _parse_date(payload["today"], "today")

    # 未来的“最后活动日期”通常意味着数据质量问题，直接拒绝比继续判断更安全。
    if last_activity_date > today:
        raise ValueError("last_activity_date 不能晚于 today")

    return OrderStatusInput(
        order_id=order_id,
        pieces=pieces,
        due_date=due_date,
        status=status,
        current_stage=current_stage,
        last_activity_date=last_activity_date,
        today=today,
    )


def judge_order_status(payload: dict[str, Any] | OrderStatusInput) -> OrderStatusResult:
    """根据 ORDER_RISK_V1 判断订单是否逾期、临期、停滞和有风险。"""
    order = _build_input(payload)

    is_in_progress = order.status == "IN_PROGRESS"
    days_to_due = (order.due_date - order.today).days
    days_since_activity = (order.today - order.last_activity_date).days

    # 只有进行中的订单才参与逾期判断；已完成订单不会因为历史 due_date 被误报为逾期。
    overdue = is_in_progress and days_to_due < 0

    # V1 产品规则：距离交期 0~7 个自然日（含边界）定义为临期。
    due_soon = is_in_progress and 0 <= days_to_due <= DUE_SOON_DAYS

    # V1 产品规则：进行中订单连续 5 个自然日或以上没有新活动记录，定义为停滞。
    stalled = is_in_progress and days_since_activity >= STALLED_DAYS

    # V1 风险组合规则：已经逾期，或同时“临期 + 停滞”，都属于有风险。
    at_risk = overdue or (due_soon and stalled)

    labels: list[str] = []
    reason_codes: list[str] = []

    if overdue:
        labels.append("OVERDUE")
        reason_codes.append("DUE_DATE_PASSED")
    if due_soon:
        labels.append("DUE_SOON")
        reason_codes.append("DUE_WITHIN_7D")
    if stalled:
        labels.append("STALLED")
        reason_codes.append("NO_ACTIVITY_5D")
    if at_risk:
        labels.append("AT_RISK")
        if overdue:
            reason_codes.append("RISK_OVERDUE")
        else:
            reason_codes.append("RISK_DUE_SOON_AND_STALLED")

    # judgement 是首屏使用的总判断；labels 保留细粒度事实，方便 UI 展开和 Tracing 追溯。
    judgement = "AT_RISK" if at_risk else "NORMAL"

    return OrderStatusResult(
        order_id=order.order_id,
        judgement=judgement,
        labels=labels,
        derived_metrics={
            "days_to_due": days_to_due,
            "days_overdue": max(-days_to_due, 0) if overdue else 0,
            "days_since_activity": days_since_activity,
        },
        reason_codes=reason_codes,
        rule_version=RULE_VERSION,
    )
