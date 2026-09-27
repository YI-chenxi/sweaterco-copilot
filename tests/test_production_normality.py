import unittest

from app.tools.production_normality import judge_production_normality


class TestNormality(unittest.TestCase):
    def rows(self, recent):
        rows = []
        # 7 个历史周一到周六/下周一，确保历史基准为 100。
        dates = [
            "2026-03-02",
            "2026-03-03",
            "2026-03-04",
            "2026-03-05",
            "2026-03-06",
            "2026-03-07",
            "2026-03-09",
        ]
        rows += [{"date": d, "stage": "ASSEMBLY", "pieces_completed": 100} for d in dates]
        rd = ["2026-03-10", "2026-03-11", "2026-03-12", "2026-03-13", "2026-03-14", "2026-03-16"]
        rows += [{"date": d, "stage": "ASSEMBLY", "pieces_completed": recent} for d in rd]
        rows += [
            {"date": "2026-03-15", "stage": "ASSEMBLY", "pieces_completed": 0}
        ]  # Sunday，应被排除
        return rows

    def test_abnormal(self):
        r = judge_production_normality(self.rows(60), "ASSEMBLY")
        self.assertEqual(r.judgement, "ABNORMAL")
        self.assertEqual(r.deviation_pct, -40.0)

    def test_watch_boundary(self):
        r = judge_production_normality(self.rows(85), "ASSEMBLY")
        self.assertEqual(r.judgement, "WATCH")

    def test_normal(self):
        r = judge_production_normality(self.rows(95), "ASSEMBLY")
        self.assertEqual(r.judgement, "NORMAL")

    def test_insufficient(self):
        with self.assertRaises(ValueError):
            judge_production_normality(self.rows(60)[-6:], "ASSEMBLY")


if __name__ == "__main__":
    unittest.main()
