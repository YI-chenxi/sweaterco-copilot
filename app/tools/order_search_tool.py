"""Deterministic tool for searching factory orders."""

import csv
from datetime import date
from pathlib import Path

from app.agent.contracts import Evidence, ToolResult
from app.tools.data_parsing import parse_order_row


class OrderSearchTool:
    """Search orders using one or more objective filters."""

    name = "search_factory_orders"
    description = (
        "Search factory orders by customer, product, category, status, "
        "current stage, or due-date range."
    )

    def __init__(self, orders_path: Path | None = None) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.orders_path = orders_path or project_root / "data" / "orders.csv"

    def execute(
        self,
        *,
        customer: str | None = None,
        product: str | None = None,
        category: str | None = None,
        status: str | None = None,
        current_stage: str | None = None,
        due_before: date | None = None,
        due_after: date | None = None,
    ) -> ToolResult:
        """Return orders matching all supplied filters."""

        filters = {
            "customer": customer,
            "product": product,
            "category": category,
            "status": status,
            "current_stage": current_stage,
        }

        normalized_filters = {
            key: value.strip().upper()
            for key, value in filters.items()
            if value is not None and value.strip()
        }
        if (
            not normalized_filters
            and due_before is None
            and due_after is None
        ):
            return ToolResult(
                data={
                    "orders": [],
                    "count": 0,
                },
                warnings=[
                    "At least one search filter must be provided."
                ],
            )

        if (
            due_before is not None
            and due_after is not None
            and due_after >= due_before
        ):
            raise ValueError(
                "due_after must be earlier than due_before."
            )

        matched_orders: list[dict[str, str]] = []

        with self.orders_path.open(newline="", encoding="utf-8") as orders_file:
            for row in csv.DictReader(orders_file):
                if any(
                    row[key].strip().upper() != expected
                    for key, expected in normalized_filters.items()
                ):
                    continue

                row_due_date = date.fromisoformat(row["due_date"])

                if due_before is not None and row_due_date >= due_before:
                    continue

                if due_after is not None and row_due_date <= due_after:
                    continue

                matched_orders.append(parse_order_row(row))

        evidence = [
            Evidence(
                source="orders.csv",
                reference=row["order_id"],
                detail="Order row matched the supplied search filters.",
            )
            for row in matched_orders
        ]

        warnings = []
        if not matched_orders:
            warnings.append("No orders matched the supplied filters.")

        return ToolResult(
            data={
                "orders": matched_orders,
                "count": len(matched_orders),
            },
            evidence=evidence,
            warnings=warnings,
        )