from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Finding:
    finding_id: str
    finding_type: str
    title: str
    severity_score: int
    urgency_score: int
    magnitude_score: int
    exposure_score: int
    evidence: dict[str, Any]
    rule_version: str
