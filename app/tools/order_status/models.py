"""订单状态判断工具的数据模型。"""

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True)
class OrderStatusInput:
    order_id: str
    pieces: int
    due_date: date
    status: str
    current_stage: str
    last_activity_date: date
    today: date


@dataclass(frozen=True)
class OrderStatusResult:
    order_id: str
    judgement: str
    labels: list[str]
    derived_metrics: dict[str, int | None]
    reason_codes: list[str]
    rule_version: str

    def to_dict(self) -> dict[str, Any]:
        """转换为便于 API / Agent 使用的字典。"""
        return asdict(self)
