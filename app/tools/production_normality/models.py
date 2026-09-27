from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class ProductionRecord:
    date: date
    stage: str
    pieces_completed: int


@dataclass(frozen=True)
class ProductionNormalityResult:
    stage: str
    historical_baseline: float
    recent_performance: float
    deviation_pct: float
    judgement: str
    direction: str
    recent_days_used: int
    historical_days_used: int
    rule_version: str
