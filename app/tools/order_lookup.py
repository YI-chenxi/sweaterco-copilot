"""Example deterministic tool for looking up one factory order."""

import csv
from pathlib import Path

from app.agent.contracts import Evidence, ToolResult


class OrderLookupTool:
    """Return an exact order record with a row-level evidence reference."""

    name = "lookup_order"
    description = "Find one order by its order ID, for example ORD-057."

    def __init__(self, orders_path: Path | None = None) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.orders_path = orders_path or project_root / "data" / "orders.csv"

    def execute(self, *, order_id: str) -> ToolResult:
        """Look up an order without inference, arithmetic, or LLM calls."""
        normalized_order_id = order_id.strip().upper()
        if not normalized_order_id:
            raise ValueError("order_id must not be empty")

        with self.orders_path.open(newline="", encoding="utf-8") as orders_file:
            for row in csv.DictReader(orders_file):
                if row["order_id"] == normalized_order_id:
                    return ToolResult(
                        data=dict(row),
                        evidence=[
                            Evidence(
                                source="orders.csv",
                                reference=normalized_order_id,
                                detail="Exact row matched by order_id.",
                            )
                        ],
                    )

        return ToolResult(
            data={},
            warnings=[f"No order with ID {normalized_order_id} exists in orders.csv."],
        )
