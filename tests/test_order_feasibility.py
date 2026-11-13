import unittest

from app.tools.order_feasibility import estimate_order_feasibility


class TestFeasibility(unittest.TestCase):
    def production(self):
        rows = []
        stages = ("KNITTING", "ASSEMBLY", "WASHING", "PACKING")
        # 13 个非周日记录/工序：历史 7 天=100，最近6天 Assembly=50，其余=100。
        dates = [
            "2026-03-02",
            "2026-03-03",
            "2026-03-04",
            "2026-03-05",
            "2026-03-06",
            "2026-03-07",
            "2026-03-09",
            "2026-03-10",
            "2026-03-11",
            "2026-03-12",
            "2026-03-13",
            "2026-03-14",
            "2026-03-16",
        ]
        for s in stages:
            for i, d in enumerate(dates):
                rows.append(
                    {
                        "date": d,
                        "stage": s,
                        "pieces_completed": 50 if s == "ASSEMBLY" and i >= 7 else 100,
                    }
                )
        return rows

    def test_conditional_with_external(self):
        req = {
            "category": "TOPS",
            "pieces": 800,
            "requested_delivery_date": "2026-04-25",
            "today": "2026-04-01",
        }
        orders = [
            {
                "status": "IN_PROGRESS",
                "due_date": "2026-04-10",
                "current_stage": "ASSEMBLY",
                "pieces": 1000,
            }
        ]
        workshops = [
            {
                "workshop_id": "W1",
                "name": "X",
                "status": "ACTIVE",
                "makes": "TOPS",
                "current_queue_days": 1,
                "pickup_lead_days": 1,
                "capacity_pieces_per_day": 100,
                "max_batch_pieces": "",
                "defect_rate": 0.05,
                "cost_per_piece": 1.2,
            }
        ]
        r = estimate_order_feasibility(req, orders, self.production(), workshops)
        self.assertEqual(r.working_days, 21)
        self.assertEqual(r.planning_rates["ASSEMBLY"], 50.0)
        self.assertEqual(r.estimate, "CONDITIONAL")

    def test_suspended_excluded(self):
        req = {
            "category": "TOPS",
            "pieces": 100,
            "requested_delivery_date": "2026-04-25",
            "today": "2026-04-01",
        }
        w = [
            {
                "workshop_id": "W1",
                "name": "X",
                "status": "SUSPENDED",
                "makes": "TOPS",
                "current_queue_days": 0,
                "pickup_lead_days": 1,
                "capacity_pieces_per_day": 100,
                "max_batch_pieces": "",
                "defect_rate": 0.0,
                "cost_per_piece": 1.0,
            }
        ]
        r = estimate_order_feasibility(req, [], self.production(), w)
        self.assertEqual(r.external_options, [])


if __name__ == "__main__":
    unittest.main()
