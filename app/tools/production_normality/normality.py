"""生产正常性判断：只基于工序日产出记录执行可审计统计规则。"""

from collections.abc import Iterable
from datetime import date, datetime
from statistics import mean
from typing import Any

from .config import (
    ABNORMAL_THRESHOLD_PCT,
    RECENT_PRODUCTION_DAYS,
    RULE_VERSION,
    WATCH_THRESHOLD_PCT,
)
from .models import ProductionNormalityResult, ProductionRecord


def _date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        return date.fromisoformat(v)
    raise TypeError("date 必须是 date 或 YYYY-MM-DD 字符串")


def _record(x: dict[str, Any] | ProductionRecord) -> ProductionRecord:
    if isinstance(x, ProductionRecord):
        return x
    if not isinstance(x, dict):
        raise TypeError("record 必须是 dict 或 ProductionRecord")
    for k in ("date", "stage", "pieces_completed"):
        if k not in x:
            raise ValueError(f"缺少必填字段: {k}")
    pieces = x["pieces_completed"]
    if isinstance(pieces, bool) or not isinstance(pieces, int) or pieces < 0:
        raise ValueError("pieces_completed 必须是非负整数")
    return ProductionRecord(_date(x["date"]), str(x["stage"]).strip().upper(), pieces)


def judge_production_normality(
    records: Iterable[dict[str, Any] | ProductionRecord], stage: str
) -> ProductionNormalityResult:
    target = str(stage).strip().upper()
    rows = sorted((_record(r) for r in records), key=lambda r: r.date)
    # 周日停产，V1 明确排除 Sunday 的 0，避免压低正常产出基准。
    rows = [r for r in rows if r.stage == target and r.date.weekday() != 6]
    if len(rows) <= RECENT_PRODUCTION_DAYS:
        raise ValueError("有效历史数据不足：需要最近 6 个生产日之外至少 1 个历史生产日")
    recent = rows[-RECENT_PRODUCTION_DAYS:]
    historical = rows[:-RECENT_PRODUCTION_DAYS]
    hist = mean(r.pieces_completed for r in historical)
    rec = mean(r.pieces_completed for r in recent)
    if hist <= 0:
        raise ValueError("historical_baseline 必须大于 0")
    deviation = (rec / hist - 1) * 100
    abs_dev = abs(deviation)
    if abs_dev >= ABNORMAL_THRESHOLD_PCT:
        judgement = "ABNORMAL"
    elif abs_dev >= WATCH_THRESHOLD_PCT:
        judgement = "WATCH"
    else:
        judgement = "NORMAL"
    direction = (
        "ABOVE_NORMAL" if deviation > 0 else "BELOW_NORMAL" if deviation < 0 else "ON_BASELINE"
    )
    return ProductionNormalityResult(
        target,
        round(hist, 1),
        round(rec, 1),
        round(deviation, 1),
        judgement,
        direction,
        len(recent),
        len(historical),
        RULE_VERSION,
    )
