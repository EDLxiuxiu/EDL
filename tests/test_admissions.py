import unittest

from tests.choice_cases import run_case
from admissions_app.core.runner import check_balances, run_all_batches
from admissions_app.core.third_batch import PLANS, build_candidates


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.candidates = build_candidates()
        self.by_id = {candidate["id"]: candidate for candidate in self.candidates}

    def run_sample(self):
        result = run_all_batches(self.candidates, PLANS)
        check_balances(PLANS, result)
        ids = [candidate_id for group in result["admitted"].values()
               for candidate_id in group]
        self.assertEqual(len(ids), len(set(ids)), "同一考生被重复录取")
        return result

    def test_baseline(self):
        result = self.run_sample()
        expected = {
            "1-A": ["S01"], "1-B": [],
            "2-A-J1": ["S06"], "2-A-J2": ["S02"],
            "2-B-J1": ["S03"], "2-B-J2": ["S08"],
            "2-C-J1": ["S05"], "2-C-J2": [],
            "3-A": ["S09", "S10"], "3-B": ["S07", "S04", "S13"],
            "3-C": ["S17", "S15", "S16"], "4-D": ["S11", "S18"],
        }
        self.assertEqual(result["admitted"], expected)
        self.assertEqual(result["balances"], {
            plan_id: 1 if plan_id == "4-D" else 0 for plan_id in expected
        })
        self.assertCountEqual(result["transfers"], [
            ("1-B", "3-B", 1),
            ("2-A-J2", "3-A", 1),
            ("2-B-J2", "3-B", 1),
            ("2-C-J2", "3-C", 1),
        ])

    def test_first_batch_and_quota_partition(self):
        result = self.run_sample()
        reasons = result["first_reasons"]
        self.assertEqual(reasons["S01"], "已录取至 1-A")
        self.assertEqual(reasons["S02"], "计划名额已满")
        self.assertEqual(reasons["S03"], "无该校自主招生资格")
        self.assertEqual(reasons["S04"], "低于控制线")
        self.assertEqual(reasons["S05"], "低于控制线")
        self.assertIn("G1 志愿1 S07 2-A-J1 名额已满", result["second_log"])
        self.assertIn("G3 志愿1 S16 2-C-J2 低于计划控制线", result["second_log"])

    def test_gradient_boundary_720_to_719(self):
        self.by_id["S07"]["score"] = 719
        admitted = self.run_sample()["admitted"]
        self.assertEqual(admitted["3-B"], ["S04", "S13", "S14"])
        self.assertFalse(any("S07" in ids for ids in admitted.values()))

    def test_tie_order_and_lowest_score(self):
        baseline = self.run_sample()["admitted"]["3-C"]
        scores = [self.by_id[candidate_id]["score"] for candidate_id in baseline]
        self.assertEqual(min(scores), 650)
        self.assertEqual((baseline[-1], scores[-1]), ("S16", 675))

        self.by_id["S16"]["tie"] = 2
        self.by_id["S19"]["tie"] = 1
        adjusted = self.run_sample()["admitted"]["3-C"]
        self.assertEqual(adjusted, ["S17", "S15", "S19"])

    def test_city_cutoff_599_to_600(self):
        baseline = self.run_sample()
        self.assertIn("S20 低于全市控制线，跳过", baseline["fourth_log"])
        self.by_id["S20"].update(score=600, tie=2)
        adjusted = self.run_sample()
        self.assertEqual(adjusted["admitted"]["4-D"], ["S11", "S18", "S20"])
        self.assertEqual(adjusted["balances"]["4-D"], 0)

    def test_fourth_batch_without_choices(self):
        for candidate in self.candidates:
            candidate["choices"][4] = []
        result = self.run_sample()
        self.assertEqual(result["admitted"]["4-D"], [])
        self.assertEqual(result["balances"]["4-D"], 3)
        self.assertEqual(sum(map(len, result["admitted"].values())), 14)

    def test_high_score_choice_changes(self):
        scenarios = [
            ("S11", ["S18"], 2, 15),
            ("S12", ["S11", "S18"], 1, 16),
        ]
        for target, fourth, balance, total in scenarios:
            with self.subTest(target=target):
                result = run_case(target)
                admitted = result["admitted"]
                self.assertEqual(admitted["3-B"], ["S07", target, "S04"])
                self.assertEqual(admitted["4-D"], fourth)
                self.assertEqual(result["balances"]["4-D"], balance)
                self.assertEqual(sum(map(len, admitted.values())), total)
                self.assertFalse(any("S13" in ids for ids in admitted.values()))

    def test_rerun_after_variants(self):
        original = self.run_sample()
        run_case("S11")
        run_case("S12")
        self.assertEqual(self.run_sample(), original)
        self.assertEqual(run_case(), original)


if __name__ == "__main__":
    unittest.main(verbosity=2)