"""订单状态判断工具自动化测试。"""

import unittest

from app.tools.order_status.order_status import judge_order_status


class JudgeOrderStatusTests(unittest.TestCase):
    def base(self, **overrides):
        payload = {
            "order_id": "ORD-TEST",
            "pieces": 1000,
            "due_date": "2026-04-20",
            "status": "IN_PROGRESS",
            "current_stage": "WASHING",
            "last_activity_date": "2026-04-01",
            "today": "2026-04-01",
        }
        payload.update(overrides)
        return payload

    def test_ord_002_overdue_and_stalled(self):
        result = judge_order_status(
            self.base(
                order_id="ORD-002",
                due_date="2026-03-29",
                last_activity_date="2026-03-23",
            )
        )
        self.assertEqual(result.judgement, "AT_RISK")
        self.assertEqual(result.derived_metrics["days_overdue"], 3)
        self.assertEqual(result.derived_metrics["days_since_activity"], 9)
        self.assertIn("OVERDUE", result.labels)
        self.assertIn("STALLED", result.labels)

    def test_due_today_is_due_soon_not_overdue(self):
        result = judge_order_status(self.base(due_date="2026-04-01"))
        self.assertIn("DUE_SOON", result.labels)
        self.assertNotIn("OVERDUE", result.labels)

    def test_exactly_five_days_is_stalled(self):
        result = judge_order_status(
            self.base(
                due_date="2026-04-06",
                last_activity_date="2026-03-27",
            )
        )
        self.assertIn("STALLED", result.labels)
        self.assertEqual(result.judgement, "AT_RISK")

    def test_due_in_eight_days_is_not_due_soon(self):
        result = judge_order_status(self.base(due_date="2026-04-09"))
        self.assertNotIn("DUE_SOON", result.labels)
        self.assertEqual(result.judgement, "NORMAL")

    def test_complete_order_is_not_risk_even_if_due_date_passed(self):
        result = judge_order_status(
            self.base(
                status="COMPLETE",
                due_date="2026-03-01",
                last_activity_date="2026-03-01",
                current_stage="COMPLETE",
            )
        )
        self.assertEqual(result.judgement, "NORMAL")
        self.assertEqual(result.labels, [])

    def test_missing_field_raises(self):
        payload = self.base()
        del payload["due_date"]
        with self.assertRaises(ValueError):
            judge_order_status(payload)

    def test_invalid_date_raises(self):
        with self.assertRaises(ValueError):
            judge_order_status(self.base(due_date="04/20/2026"))

    def test_future_last_activity_raises(self):
        with self.assertRaises(ValueError):
            judge_order_status(self.base(last_activity_date="2026-04-02"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
