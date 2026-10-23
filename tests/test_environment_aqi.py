import unittest

from app.environment import cpcb_pm_index, cpcb_pm_subindex, construction_site_scenario


class TestCpcbPmIndex(unittest.TestCase):
    def test_pm25_breakpoints(self):
        self.assertEqual(cpcb_pm_subindex(0, "pm25"), 0)
        self.assertEqual(cpcb_pm_subindex(30, "pm25"), 50)
        self.assertEqual(cpcb_pm_subindex(31, "pm25"), 51)
        self.assertEqual(cpcb_pm_subindex(60, "pm25"), 100)
        self.assertEqual(cpcb_pm_subindex(250, "pm25"), 400)
        self.assertEqual(cpcb_pm_subindex(251, "pm25"), 500)

    def test_pm10_breakpoints(self):
        self.assertEqual(cpcb_pm_subindex(0, "pm10"), 0)
        self.assertEqual(cpcb_pm_subindex(50, "pm10"), 50)
        self.assertEqual(cpcb_pm_subindex(51, "pm10"), 51)
        self.assertEqual(cpcb_pm_subindex(430, "pm10"), 400)
        self.assertEqual(cpcb_pm_subindex(431, "pm10"), 500)

    def test_pm_only_index_uses_worst_subindex_and_category(self):
        result = cpcb_pm_index(91, 101)
        self.assertEqual(result["subindices"], {"pm25": 201, "pm10": 101})
        self.assertEqual(result["index"], 201)
        self.assertEqual(result["category"], "Poor")
        self.assertIn("NOT OFFICIAL", result["scope"])

    def test_invalid_pollutant_and_concentration_are_rejected(self):
        with self.assertRaises(ValueError):
            cpcb_pm_subindex(10, "no2")
        with self.assertRaises(ValueError):
            cpcb_pm_subindex(-1, "pm25")
        with self.assertRaises(ValueError):
            cpcb_pm_subindex(float("nan"), "pm10")

    def test_construction_scenario_uses_activity_and_control_assumptions(self):
        uncontrolled = construction_site_scenario(20, 40, "typical", "none")
        controlled = construction_site_scenario(20, 40, "typical", "strong")
        self.assertEqual(uncontrolled["pm25"], 32)
        self.assertEqual(uncontrolled["pm10"], 75)
        self.assertEqual(controlled["pm25"], 24.8)
        self.assertEqual(controlled["pm10"], 54)
        self.assertLess(controlled["index"], uncontrolled["index"])
        self.assertIn("NOT MEASURED", controlled["scope"])

    def test_construction_scenario_rejects_invalid_inputs(self):
        for value in (-1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                construction_site_scenario(value, 20, "typical", "basic")
        with self.assertRaises(ValueError):
            construction_site_scenario(10, 20, "unknown", "basic")
        with self.assertRaises(ValueError):
            construction_site_scenario(10, 20, "typical", "unknown")


if __name__ == "__main__":
    unittest.main()
