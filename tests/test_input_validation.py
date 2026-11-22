import unittest
from unittest.mock import patch

from admissions_app.core.runner import run_all_batches
from admissions_app.core.third_batch import PLANS, build_candidates


class InputValidationTests(unittest.TestCase):
    def setUp(self):
        self.candidates = build_candidates()
        self.by_id = {
            candidate["id"]: candidate for candidate in self.candidates
        }

    def assert_rejected_before_admission(self, expected_message):
        # 用模拟函数记录第一批函数是否被调用。
        with patch("admissions_app.core.runner.run_first_batch") as first_batch:
            with self.assertRaises(ValueError) as error:
                run_all_batches(self.candidates, PLANS)
            self.assertIn(expected_message, str(error.exception))
            first_batch.assert_not_called()

    def test_duplicate_candidate_id(self):
        self.candidates.append(self.by_id["S06"].copy())
        self.assert_rejected_before_admission("重复考生编号：S06")

    def test_negative_score(self):
        self.by_id["S18"]["score"] = -1
        self.assert_rejected_before_admission("S18 的总分必须是非负整数")

    def test_duplicate_school_in_choices(self):
        self.by_id["S11"]["choices"][3] = ["A", "A"]
        self.assert_rejected_before_admission("S11 第 3 批重复填报学校 A")

    def test_school_with_no_plan_in_batch(self):
        self.by_id["S11"]["choices"][3] = ["A", "D"]
        self.assert_rejected_before_admission(
            "S11 第 3 批填报的学校 D 无招生计划"
        )

    def test_duplicate_tie_for_same_score(self):
        self.by_id["S19"]["tie"] = 1
        self.assert_rejected_before_admission(
            "S19 与 S16 的同分序号重复：675 分、序号 1"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)