"""接单可行性估算：保守负载代理 + 历史/近期规划速率 + 外协容量。"""

from datetime import date, datetime, timedelta
from math import ceil
from statistics import mean
from typing import Any

from .config import RECENT_PRODUCTION_DAYS, RULE_VERSION, STAGES
from .models import FeasibilityResult


def _date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        return date.fromisoformat(v)
    raise TypeError("日期必须是 date 或 YYYY-MM-DD")


def _working_days(start: date, end: date) -> int:
    if end < start:
        return 0
    n = 0
    d = start
    while d <= end:
        if d.weekday() != 6:
            n += 1
        d += timedelta(days=1)
    return n


def estimate_order_feasibility(
    request: dict[str, Any],
    orders: list[dict[str, Any]],
    production: list[dict[str, Any]],
    workshops: list[dict[str, Any]],
) -> FeasibilityResult:
    for k in ("category", "pieces", "requested_delivery_date", "today"):
        if k not in request:
            raise ValueError(f"request 缺少字段: {k}")
    pieces = int(request["pieces"])
    if pieces <= 0:
        raise ValueError("pieces 必须大于 0")
    today = _date(request["today"])
    deadline = _date(request["requested_delivery_date"])
    if deadline <= today:
        raise ValueError("requested_delivery_date 必须晚于 today")
    category = str(request["category"]).upper()
    working = _working_days(today + timedelta(days=1), deadline)
    planning = {}
    for stage in STAGES:
        rows = []
        for r in production:
            if str(r["stage"]).upper() == stage:
                d = _date(r["date"])
                if d.weekday() != 6:
                    rows.append((d, int(r["pieces_completed"])))
        rows.sort()
        if len(rows) <= RECENT_PRODUCTION_DAYS:
            raise ValueError(f"{stage} 历史生产数据不足")
        hist = mean(v for _, v in rows[:-RECENT_PRODUCTION_DAYS])
        recent = mean(v for _, v in rows[-RECENT_PRODUCTION_DAYS:])
        # V1 保守规划：历史基准与最近一周表现取较低者。
        planning[stage] = round(min(hist, recent), 1)
    capacity = {s: round(planning[s] * working, 1) for s in STAGES}
    existing = {s: 0 for s in STAGES}
    for o in orders:
        if str(o.get("status", "")).upper() != "IN_PROGRESS":
            continue
        due = _date(o["due_date"])
        if due > deadline:
            continue
        stage = str(o["current_stage"]).upper()
        if stage not in STAGES:
            continue
        qty = int(o["pieces"])
        idx = STAGES.index(stage)
        # 数据没有真实剩余 WIP；V1 保守假设全部件数仍需经过当前及后续工序。
        for s in STAGES[idx:]:
            existing[s] += qty
    demand = {s: existing[s] + pieces for s in STAGES}
    margin = {s: round(capacity[s] - demand[s], 1) for s in STAGES}
    options = []
    for w in workshops:
        status = str(w.get("status", "")).upper()
        makes = str(w.get("makes", "")).upper()
        if status != "ACTIVE" or category not in makes:
            continue
        delay = ceil(float(w["current_queue_days"]) + float(w["pickup_lead_days"]))
        start = today + timedelta(days=delay)
        days = _working_days(start, deadline)
        nominal = days * int(w["capacity_pieces_per_day"])
        cap = w.get("max_batch_pieces")
        if cap not in (None, "", "nan"):
            try:
                nominal = min(nominal, int(float(cap)))
            except (TypeError, ValueError):
                pass
        defect = float(w["defect_rate"])
        options.append(
            {
                "workshop_id": w.get("workshop_id"),
                "name": w.get("name"),
                "delay_days": delay,
                "available_production_days": days,
                "nominal_capacity": nominal,
                "quality_adjusted_capacity": round(nominal * (1 - defect), 1),
                "defect_rate": defect,
                "cost_per_piece": float(w["cost_per_piece"]),
                "can_cover_new_order_nominally": nominal >= pieces,
            }
        )
    all_internal = all(v >= 0 for v in margin.values())
    external_cover = any(x["can_cover_new_order_nominally"] for x in options)
    # 有真实 WIP 缺口，因此 V1 只在保守代理仍有余量时给 SUPPORTED；否则有外协覆盖则给 CONDITIONAL。
    estimate = "SUPPORTED" if all_internal else "CONDITIONAL" if external_cover else "NOT_SUPPORTED"
    assumptions = [
        "Sunday（周日）不计生产日",
        "Planning Rate（规划速率）取历史基准与最近 6 个生产日均值的较低者",
        "Existing Load Proxy（现有负载代理）假设当前工序订单全部件数仍需经过当前及后续工序",
        "Quality-adjusted Capacity（质量调整产能）仅为缺陷率情景估算，不是原始事实",
    ]
    return FeasibilityResult(
        estimate, working, planning, capacity, demand, margin, options, assumptions, RULE_VERSION
    )
