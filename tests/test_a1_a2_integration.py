"""A1 -> A2 集成测试：使用 A1 ToolResult 形状的 fake，验证真实数据契约。"""

import unittest
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.tools.a2_integration import (
    discover_factory_risks,
    estimate_feasibility_from_retrieval,
    judge_order_from_retrieval,
    judge_production_from_retrieval,
)


@dataclass
class FakeToolResult:
    data: dict
    evidence: list = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class FakeOrderSearch:
    def __init__(self, orders):
        self.orders = orders

    def execute(self, **kwargs):
        return FakeToolResult(
            {"orders": self.orders, "count": len(self.orders)}, ["orders-evidence"]
        )


class FakeProduction:
    def __init__(self, by_stage):
        self.by_stage = by_stage

    def execute(self, **kwargs):
        stage = kwargs["stage"]
        rows = self.by_stage.get(stage, [])
        return FakeToolResult(
            {"records": rows, "count": len(rows), "summary": {}}, [f"prod:{stage}"]
        )


class FakeWorkshops:
    def __init__(self, workshops):
        self.workshops = workshops

    def execute(self, **kwargs):
        return FakeToolResult(
            {"workshops": self.workshops, "count": len(self.workshops)}, ["workshop-evidence"]
        )


def production_rows(stage, historical=800, recent=400):
    rows = []
    d = date(2026, 1, 1)
    produced = 0
    while produced < 14:
        if d.weekday() != 6:
            value = historical if produced < 8 else recent
            rows.append({"date": d.isoformat(), "stage": stage, "pieces_completed": value})
            produced += 1
        d += timedelta(days=1)
    return rows


class IntegrationTests(unittest.TestCase):
    def test_order_retrieval_to_judgement_preserves_evidence(self):
        src = FakeToolResult(
            {
                "order_id": "ORD-002",
                "pieces": 1000,
                "due_date": "2026-03-29",
                "status": "IN_PROGRESS",
                "current_stage": "WASHING",
                "last_activity_date": "2026-03-23",
            },
            ["row:ORD-002"],
        )
        out = judge_order_from_retrieval(src, today="2026-04-01")
        self.assertEqual(out.data["judgement"], "AT_RISK")
        self.assertEqual(out.data["derived_metrics"]["days_overdue"], 3)
        self.assertEqual(out.evidence, ["row:ORD-002"])

    def test_production_retrieval_to_normality(self):
        rows = production_rows("ASSEMBLY", 800, 400)
        src = FakeToolResult(
            {"records": rows, "count": len(rows), "summary": {}}, ["prod-evidence"]
        )
        out = judge_production_from_retrieval(src, stage="ASSEMBLY")
        self.assertEqual(out.data["judgement"], "ABNORMAL")
        self.assertEqual(out.evidence, ["prod-evidence"])

    def test_discovery_pipeline_returns_ranked_findings(self):
        orders = [
            {
                "order_id": "ORD-002",
                "pieces": 1000,
                "due_date": "2026-03-29",
                "status": "IN_PROGRESS",
                "current_stage": "WASHING",
                "last_activity_date": "2026-03-23",
            }
        ]
        prod = {
            s: production_rows(s, 800, 400 if s == "ASSEMBLY" else 800)
            for s in ("KNITTING", "ASSEMBLY", "WASHING", "PACKING")
        }
        out = discover_factory_risks(
            order_search_tool=FakeOrderSearch(orders),
            production_retrieval_tool=FakeProduction(prod),
            today="2026-04-01",
            top_n=5,
        )
        self.assertGreaterEqual(out.data["count"], 2)
        ids = {x["finding_id"] for x in out.data["findings"]}
        self.assertIn("ORDER:ORD-002", ids)
        self.assertIn("STAGE:ASSEMBLY", ids)
        self.assertTrue(out.evidence)

    def test_feasibility_pipeline_uses_retrieved_facts(self):
        orders = []
        prod = {
            s: production_rows(s, 800, 800) for s in ("KNITTING", "ASSEMBLY", "WASHING", "PACKING")
        }
        workshops = [
            {
                "workshop_id": "W1",
                "name": "QuickStitch",
                "capacity_pieces_per_day": 300,
                "pickup_lead_days": 1,
                "defect_rate": 0.12,
                "cost_per_piece": 1.5,
                "makes": "TOPS",
                "status": "ACTIVE",
                "max_batch_pieces": None,
                "current_queue_days": 4.0,
            }
        ]
        request = {
            "category": "TOPS",
            "pieces": 800,
            "requested_delivery_date": "2026-04-25",
            "today": "2026-04-01",
        }
        out = estimate_feasibility_from_retrieval(
            request=request,
            order_search_tool=FakeOrderSearch(orders),
            production_retrieval_tool=FakeProduction(prod),
            workshop_retrieval_tool=FakeWorkshops(workshops),
        )
        self.assertEqual(out.data["estimate"], "SUPPORTED")
        self.assertEqual(out.data["working_days"], 21)
        self.assertTrue(out.evidence)


if __name__ == "__main__":
    unittest.main()
