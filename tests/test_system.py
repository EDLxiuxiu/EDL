import copy
import csv
import io
import json
import unittest
from unittest.mock import patch

from admissions_app.domain import Rules, advance, digest, edit_entity, initial_state, projection, run_complete, sample_dataset, validate
from admissions_app.repository import Conflict, MemoryRepository, MySQLRepository, packed, unpacked
from admissions_app.service import Service
from admissions_app.web import create_app
from run_admissions import run_all_batches


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.data, self.rules = sample_dataset(), Rules()

    def test_full_legacy_equivalence_and_last_candidate(self):
        result = run_complete(self.data, self.rules)
        original = run_all_batches(self.data["candidates"], self.data["plans"], self.data["autonomous_ranks"])
        for key in original:
            self.assertEqual(result[key], original[key])
        rows, plans = projection(self.data, self.rules, result)
        c = next(p for p in plans if p["plan_id"] == "3-C")
        self.assertEqual((c["lowest_score"], c["last_id"], c["last_score"], c["last_choice"], c["last_tie"]), (650, "S16", 675, 2, 1))
        events = next(r for r in rows if r["candidate"]["id"] == "S16")["events"]
        accepted = [e for e in events if e["message"].endswith("已录取")]
        self.assertEqual((accepted[-1]["balance_before"], accepted[-1]["balance_after"]), (1, 0))

    def test_batch_resume_and_no_duplicate_transfer(self):
        state = initial_state(self.data)
        frozen = copy.deepcopy(state)
        for _ in range(4):
            state = advance(self.data, self.rules, state)
        self.assertEqual(initial_state(self.data), frozen)
        self.assertEqual(state, run_complete(self.data, self.rules))
        self.assertEqual(len(state["transfers"]), 4)
        with self.assertRaises(ValueError):
            advance(self.data, self.rules, state)

    def test_parameters_and_default_independence(self):
        self.assertEqual(Rules(city_cutoff=492, first_gradient=712).gradient_lines, (712, 672, 632, 592, 552, 512, 492))
        low = run_complete(self.data, Rules(city_cutoff=599, first_gradient=720))
        self.assertNotIn("S20", low["admitted"]["4-D"])  # 计划控制线仍为 600。
        lowered_plan = copy.deepcopy(self.data)
        lowered_plan["plans"]["4-D"]["cutoff"] = 599
        low = run_complete(lowered_plan, Rules(city_cutoff=599, first_gradient=720))
        self.assertIn("S20", low["admitted"]["4-D"])
        self.assertNotIn("S20", run_complete(self.data, self.rules)["admitted"]["4-D"])
        for kwargs in ({"interval": 0}, {"city_cutoff": 721}, {"city_cutoff": True}):
            with self.assertRaises(ValueError):
                Rules(**kwargs)

    def test_dataset_validation_references_and_rank(self):
        changes = [lambda d: d["plans"]["3-A"].update(seats=-1),
                   lambda d: d["senior_schools"]["A"].update(general_batch=4),
                   lambda d: d["candidates"][0].update(junior="missing"),
                   lambda d: d["autonomous_ranks"]["A"].update(S02=1),
                   lambda d: d["plans"].pop("3-A")]
        for change in changes:
            d = copy.deepcopy(self.data)
            change(d)
            with self.assertRaises(ValueError):
                validate(d, self.rules)

    def test_two_independent_choice_variants(self):
        for cid, expected_count, expected_b in [("S11", 15, ["S07", "S11", "S04"]), ("S12", 16, ["S07", "S12", "S04"])]:
            c = copy.deepcopy(next(c for c in self.data["candidates"] if c["id"] == cid))
            c["choices"][3] = ["B", "A"]
            d = edit_entity(self.data, "candidates", cid, c)
            result = run_complete(d, self.rules)
            self.assertEqual(result["admitted"]["3-B"], expected_b)
            self.assertEqual(sum(map(len, result["admitted"].values())), expected_count)
        self.assertEqual(next(c for c in self.data["candidates"] if c["id"] == "S11")["choices"][3], ["A", "B"])

    def test_checkpoint_codec_and_null_empty_plan(self):
        state = run_complete(self.data, self.rules)
        restored = unpacked(packed(state))
        self.assertEqual(digest(restored), digest(state))
        _, plans = projection(self.data, self.rules, state)
        empty = next(p for p in plans if p["plan_id"] == "1-B")
        self.assertIsNone(empty["lowest_score"])
        self.assertIsNone(empty["last_tie"])

    def test_candidate_number_can_equal_gradient_label(self):
        next(c for c in self.data["candidates"] if c["id"] == "S19")["id"] = "G3"
        rows, _ = projection(self.data, self.rules, run_complete(self.data, self.rules))
        aliased = next(r for r in rows if r["candidate"]["id"] == "G3")
        for event in aliased["events"]:
            parts = event["message"].split()
            if event["plan_id"]:
                self.assertEqual(parts[2], "G3")


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.repo = MemoryRepository()
        self.dataset_id = self.repo.put_dataset(sample_dataset(), "原始输入")
        self.run_id = self.repo.create_run(self.dataset_id, Rules(), {})
        self.service = Service(self.repo)

    def test_save_resume_and_stale_writer(self):
        data, rules, old = self.repo.load(self.run_id)
        state = self.service.execute(self.run_id)
        rows, plans = projection(data, rules, state)
        with self.assertRaises(Conflict):
            self.repo.save_step(self.run_id, 0, state, rows, plans)
        self.service.execute(self.run_id, full=True)
        self.assertEqual(self.repo.run(self.run_id)["admitted_count"], 16)
        self.assertEqual(self.repo.audit(self.run_id)["transfers"], 4)

    def test_save_failure_keeps_checkpoint(self):
        with patch.object(self.repo, "save_step", side_effect=RuntimeError("模拟保存前故障")):
            with self.assertRaises(RuntimeError):
                self.service.execute(self.run_id)
        self.assertEqual(self.repo.load(self.run_id)[2]["completed_batch"], 0)
        self.service.execute(self.run_id, full=True)
        self.assertEqual(self.repo.audit(self.run_id)["completed_batch"], 4)

    def test_immutable_input_and_filtering(self):
        data = self.repo.dataset(self.dataset_id)
        data["candidates"][0]["name"] = "新姓名"
        new_id = self.repo.put_dataset(data, "新版本", self.dataset_id)
        self.assertNotEqual(self.repo.dataset(new_id), self.repo.load(self.run_id)[0])
        self.service.execute(self.run_id, full=True)
        rows, count = self.repo.candidates(self.run_id, {"school": "B", "batch": "3"})
        self.assertEqual([r["candidate_no"] for r in rows], ["S07", "S04", "S13"])
        self.assertEqual(count, 3)
        overview = self.repo.overview(self.run_id)
        self.assertEqual(sum(r["candidate_count"] for r in overview["scores"]), 20)
        self.assertEqual(sum(r["admitted_count"] for r in overview["juniors"]), 16)

    def test_drift_detected_and_historical_write_guard(self):
        self.repo._runs[self.run_id]["plans"][0]["remaining_seats"] += 1
        with self.assertRaises(ValueError):
            self.repo.audit(self.run_id)
        with self.assertRaises(ValueError):
            MySQLRepository("unused", database="admissionsim_scale_150000")

    def test_transaction_rollback_contract(self):
        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *args): return False
        class Connection:
            committed, rolled = False, False
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def start_transaction(self, **kwargs): pass
            def cursor(self, **kwargs): return Cursor()
            def commit(self): self.committed = True
            def rollback(self): self.rolled = True
        repository = MySQLRepository("unused")
        con = Connection()
        with patch.object(repository, "connect", return_value=con):
            with self.assertRaises(RuntimeError):
                with repository.transaction():
                    raise RuntimeError("模拟事务故障")
        self.assertTrue(con.rolled)
        self.assertFalse(con.committed)


class WebTests(unittest.TestCase):
    def setUp(self):
        self.repo = MemoryRepository()
        self.dataset_id = self.repo.put_dataset(sample_dataset(), "样例")
        self.run_id = self.repo.create_run(self.dataset_id, Rules(), {})
        Service(self.repo).execute(self.run_id, full=True)
        self.app = create_app(self.repo, testing=True)
        self.client = self.app.test_client()
        self.client.get("/")

    def token(self):
        with self.client.session_transaction() as s:
            return s["csrf"]

    def test_all_pages_and_trace(self):
        for path in ["/", f"/datasets/{self.dataset_id}", f"/runs/{self.run_id}",
                     f"/runs/{self.run_id}?view=plans", f"/runs/{self.run_id}/candidates/S16",
                     f"/runs/{self.run_id}/transfers"]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn("内存演示", response.get_data(as_text=True))
        response = self.client.get(f"/runs/{self.run_id}?view=plans").get_data(as_text=True)
        self.assertIn("末位考生", response)

    def test_csrf_and_rule_validation(self):
        self.assertEqual(self.client.post("/datasets/sample").status_code, 400)
        response = self.client.post("/runs", data={"csrf": self.token(), "dataset_id": self.dataset_id, "interval": 0})
        self.assertEqual(response.status_code, 400)

    def test_independent_presets_and_candidate_edit(self):
        response = self.client.post("/datasets/sample", data={"csrf": self.token(), "variant": "A"})
        self.assertEqual(response.status_code, 302)
        latest = self.repo.datasets()[0]["dataset_id"]
        self.assertEqual(next(c for c in self.repo.dataset(latest)["candidates"] if c["id"] == "S11")["choices"][3], ["B", "A"])
        response = self.client.post(f"/datasets/{self.dataset_id}", data={"csrf": self.token(), "kind": "candidates", "key": "S01",
                                   "editor": "candidate", "name": "编辑后的虚构姓名", "junior": "J1", "score": 700, "tie": 1,
                                   "choice": "A", "second_choices": "A", "third_choices": "A", "fourth_choices": "D", "label": "修改姓名"})
        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(self.repo.datasets()[0]["dataset_id"], self.dataset_id)

    def test_csv_filter_and_formula_escaping(self):
        # 直接改演示投影仅用于验证导出转义；不作为业务验收数据。
        self.repo._runs[self.run_id]["candidates"][0]["candidate"]["name"] = "=1+1"
        response = self.client.get(f"/runs/{self.run_id}/export/candidates?q=S01")
        text = response.get_data(as_text=True).lstrip("\ufeff")
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "'=1+1")
        self.assertEqual(rows[0]["run_id"], self.run_id)

    def test_status_and_audit(self):
        response = self.client.get(f"/runs/{self.run_id}/status")
        self.assertEqual(response.json["completed_batch"], 4)
        response = self.client.post(f"/runs/{self.run_id}/audit", data={"csrf": self.token()}, follow_redirects=True)
        self.assertIn("完整一致性核对通过", response.get_data(as_text=True))

    def test_statistics_export_contains_all_dimensions(self):
        response = self.client.get(f"/runs/{self.run_id}/statistics.csv")
        rows = list(csv.DictReader(io.StringIO(response.get_data(as_text=True).lstrip("\ufeff"))))
        self.assertEqual({r["dimension"] for r in rows}, {"overall", "batch", "score", "junior", "senior"})
        self.assertEqual(sum(int(r["admitted_count"]) for r in rows if r["dimension"] == "senior"), 16)
        self.assertEqual(sum(int(r["candidate_count"]) for r in rows if r["dimension"] == "score"), 20)


if __name__ == "__main__":
    unittest.main()
