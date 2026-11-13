"""生产风险发现：把已计算的订单/工序事实转为透明评分并选 Top N。"""

from typing import Any

from .config import RULE_VERSION, TOP_N
from .models import Finding


def _exposure(pieces: int) -> int:
    if pieces >= 1500:
        return 3
    if pieces >= 800:
        return 2
    if pieces >= 300:
        return 1
    return 0


def _order_finding(o: dict[str, Any]) -> Finding | None:
    required = {"order_id", "pieces", "days_to_due", "days_since_activity", "current_stage"}
    missing = required - o.keys()
    if missing:
        raise ValueError(f"订单风险输入缺少字段: {', '.join(sorted(missing))}")
    dtd = int(o["days_to_due"])
    inactive = int(o["days_since_activity"])
    pieces = int(o["pieces"])
    # Urgency（紧迫度）：逾期越久，或交期越近，分数越高。
    urgency = 3 if dtd <= -7 else 2 if dtd < 0 else 1 if dtd <= 7 else 0
    # Magnitude（异常幅度）：V1 用无活动天数作为订单停滞强度代理。
    magnitude = 3 if inactive >= 7 else 2 if inactive >= 5 else 1 if inactive >= 3 else 0
    exposure = _exposure(pieces)
    score = urgency + magnitude + exposure
    if score == 0:
        return None
    return Finding(
        f"ORDER:{o['order_id']}",
        "ORDER_RISK",
        f"{o['order_id']} 订单风险",
        score,
        urgency,
        magnitude,
        exposure,
        {
            "pieces": pieces,
            "days_to_due": dtd,
            "days_since_activity": inactive,
            "current_stage": o["current_stage"],
        },
        RULE_VERSION,
    )


def _production_finding(p: dict[str, Any]) -> Finding | None:
    for k in ("stage", "deviation_pct", "recent_performance", "historical_baseline"):
        if k not in p:
            raise ValueError(f"生产异常输入缺少字段: {k}")
    dev = float(p["deviation_pct"])
    absdev = abs(dev)
    magnitude = 3 if absdev >= 30 else 2 if absdev >= 15 else 0
    if magnitude == 0:
        return None
    # 生产异常没有订单件数可用于 Exposure（业务暴露），V1 不虚构该维度，记 0。
    urgency = 3 if dev <= -30 else 2
    score = urgency + magnitude
    return Finding(
        f"STAGE:{p['stage']}",
        "PRODUCTION_ANOMALY",
        f"{p['stage']} 产出异常",
        score,
        urgency,
        magnitude,
        0,
        {
            "deviation_pct": dev,
            "recent_performance": p["recent_performance"],
            "historical_baseline": p["historical_baseline"],
        },
        RULE_VERSION,
    )


def discover_production_risks(
    order_signals: list[dict[str, Any]],
    production_signals: list[dict[str, Any]],
    top_n: int = TOP_N,
) -> list[Finding]:
    if top_n <= 0:
        raise ValueError("top_n 必须大于 0")
    findings = []
    findings += [f for o in order_signals if (f := _order_finding(o))]
    findings += [f for p in production_signals if (f := _production_finding(p))]
    # 同分时按 finding_id 固定排序，保证同一输入每次返回相同结果，便于测试和审计。
    return sorted(findings, key=lambda f: (-f.severity_score, f.finding_id))[:top_n]
