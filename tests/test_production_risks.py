import unittest

from app.tools.production_risks import discover_production_risks


class TestDiscovery(unittest.TestCase):
    def test_rank_and_topn(self):
        orders = [
            {
                "order_id": "ORD-A",
                "pieces": 2000,
                "days_to_due": -10,
                "days_since_activity": 8,
                "current_stage": "PACKING",
            },
            {
                "order_id": "ORD-B",
                "pieces": 300,
                "days_to_due": 3,
                "days_since_activity": 1,
                "current_stage": "KNITTING",
            },
        ]
        prod = [
            {
                "stage": "ASSEMBLY",
                "deviation_pct": -45.1,
                "recent_performance": 425.5,
                "historical_baseline": 774.9,
            }
        ]
        r = discover_production_risks(orders, prod, top_n=2)
        self.assertEqual(len(r), 2)
        self.assertEqual(r[0].finding_id, "ORDER:ORD-A")

    def test_normal_production_not_finding(self):
        r = discover_production_risks(
            [],
            [
                {
                    "stage": "PACKING",
                    "deviation_pct": -0.5,
                    "recent_performance": 654.5,
                    "historical_baseline": 657.6,
                }
            ],
        )
        self.assertEqual(r, [])

    def test_invalid_topn(self):
        with self.assertRaises(ValueError):
            discover_production_risks([], [], 0)


if __name__ == "__main__":
    unittest.main()
