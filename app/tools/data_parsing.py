"""Shared parsing helpers for deterministic factory data tools."""

from typing import Any


def _optional_int(value: str) -> int | None:
    """Convert an optional CSV integer field to int or None."""
    cleaned = value.strip()
    return int(cleaned) if cleaned else None


def _optional_float(value: str) -> float | None:
    """Convert an optional CSV numeric field to float or None."""
    cleaned = value.strip()
    return float(cleaned) if cleaned else None


def parse_order_row(row: dict[str, str]) -> dict[str, Any]:
    """Convert an orders.csv row into typed business facts."""
    parsed = dict(row)

    parsed["pieces"] = int(row["pieces"])

    if row["days_late"].strip():
        parsed["days_late"] = float(row["days_late"])
    else:
        parsed["days_late"] = None

    if not row["completed_date"].strip():
        parsed["completed_date"] = None

    return parsed


def parse_production_row(row: dict[str, str]) -> dict[str, Any]:
    """Convert a production_log.csv row into typed business facts."""
    parsed = dict(row)

    parsed["pieces_completed"] = int(row["pieces_completed"])

    return parsed


def parse_workshop_row(row: dict[str, str]) -> dict[str, Any]:
    """Convert a workshops.csv row into typed business facts."""
    parsed = dict(row)

    parsed["capacity_pieces_per_day"] = int(
        row["capacity_pieces_per_day"]
    )
    parsed["pickup_lead_days"] = int(row["pickup_lead_days"])
    parsed["defect_rate"] = float(row["defect_rate"])
    parsed["cost_per_piece"] = float(row["cost_per_piece"])
    parsed["max_batch_pieces"] = _optional_int(
        row["max_batch_pieces"]
    )
    parsed["current_queue_days"] = _optional_float(
        row["current_queue_days"]
    )

    return parsed