import unittest
from engine import build_recommendations, simulate_deal, ModelInputError

ASSUMPTIONS = {"expense_ratio": .10, "other_cost_ratio": .02, "target_profit_margin": .06, "min_policy_count": 100, "max_commission_rate": .25}


def cohort(slr, count=300):
    return {"Cohort_ID": "A", "Product": "Motor", "Segment": "Retail", "Region": "West", "Risk_Tier": "Low", "Channel": "Partner", "Policy_Count": count, "Expected_SLR": slr, "Written_Premium_INR": 1_000_000}


def market(low=.08, mid=.10, high=.12):
    return {"Cohort_ID": "A", "Competitor_1_Pct": low, "Competitor_2_Pct": mid, "Competitor_3_Pct": high}


class EngineTests(unittest.TestCase):
    def test_healthy_cohort_tracks_market(self):
        row = build_recommendations([cohort(.60)], [market()], ASSUMPTIONS)[0]
        self.assertAlmostEqual(row["Recommended_Pct"], .10)
        self.assertEqual(row["Status"], "Market aligned")

    def test_capacity_caps_market(self):
        row = build_recommendations([cohort(.77)], [market()], ASSUMPTIONS)[0]
        self.assertAlmostEqual(row["Max_Affordable_Pct"], .05)
        self.assertAlmostEqual(row["Recommended_Pct"], .05)
        self.assertEqual(row["Status"], "Manual review")
        self.assertAlmostEqual(row["Expected_Profit_Margin_Pct"], .06)

    def test_infeasible_even_at_zero(self):
        row = build_recommendations([cohort(.87)], [market()], ASSUMPTIONS)[0]
        self.assertEqual(row["Recommended_Pct"], 0)
        self.assertLess(row["Expected_Profit_Margin_Pct"], .06)
        self.assertIn("infeasible", row["Why"])

    def test_low_credibility(self):
        row = build_recommendations([cohort(.60, count=5)], [market()], ASSUMPTIONS)[0]
        self.assertEqual(row["Status"], "Manual review")
        self.assertIn("Limited", row["Why"])

    def test_override_breaches_target(self):
        row = build_recommendations([cohort(.77)], [market()], ASSUMPTIONS)[0]
        sim = simulate_deal(row, 1_000_000, .10, ASSUMPTIONS)
        self.assertAlmostEqual(sim["expected_margin"], .01)
        self.assertFalse(sim["meets_profit_target"])

    def test_deal_specific_terms_reprice_commission(self):
        row = build_recommendations([cohort(.60)], [market()], ASSUMPTIONS)[0]
        base = simulate_deal(row, 1_000_000, .10, ASSUMPTIONS)
        stressed = simulate_deal(row, 1_000_000, .10, ASSUMPTIONS, 3, .02)
        self.assertAlmostEqual(base["expected_margin"] - stressed["expected_margin"], .05)

    def test_percent_encoding_rejected(self):
        with self.assertRaises(ModelInputError):
            build_recommendations([cohort(77)], [market()], ASSUMPTIONS)


if __name__ == "__main__":
    unittest.main()
