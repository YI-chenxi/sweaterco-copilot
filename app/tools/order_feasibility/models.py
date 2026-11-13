from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FeasibilityResult:
    estimate: str
    working_days: int
    planning_rates: dict[str, float]
    internal_capacity: dict[str, float]
    demand_proxy: dict[str, int]
    capacity_margin: dict[str, float]
    external_options: list[dict[str, Any]]
    assumptions: list[str]
    rule_version: str
