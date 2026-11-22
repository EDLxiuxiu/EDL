"""显式启用的真实 MySQL 故障验收；默认跳过，永不操作两个历史库。"""
import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from admissions_app.domain import Rules, advance, projection, sample_dataset
from admissions_app.repository import Conflict, MySQLRepository
from admissions_app.service import Service, provenance


@unittest.skipUnless(os.environ.get("ADMISSION_MYSQL_TEST_CREDENTIAL_FILE"), "真实 MySQL 测试须显式指定已授权的本机凭据文件")
class MySQLIntegrationTests(unittest.TestCase):
    def test_persisted_web_and_filtered_csv(self):
        import csv
        import io
        from admissions_app.web import create_app
        out = Path(os.environ["ADMISSION_MYSQL_TEST_OUT"])
        out.mkdir(parents=True, exist_ok=False)
        secret = Path(os.environ["ADMISSION_MYSQL_TEST_CREDENTIAL_FILE"]).read_text(encoding="utf-8-sig").strip()
        acceptance = json.loads(Path("experiments/app_mysql_acceptance_v1/acceptance.json").read_text(encoding="utf-8"))
        run_id = acceptance["ids"]["scale_run"]
        report = {"status": "running", "mode": "flask_test_client_with_real_mysql", "run_id": run_id,
                  "source": provenance(), "checks": [], "password_recorded": False}
        path = out / "web_mysql_verification.json"
        def save():
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        save()
        try:
            repo = MySQLRepository(secret)
            client = create_app(repo, testing=True).test_client()
            for route in ["/", f"/runs/{run_id}?view=plans", f"/runs/{run_id}/candidates/S099797"]:
                response = client.get(route)
                self.assertEqual(response.status_code, 200)
                body = response.get_data(as_text=True)
                if "/runs/" in route and "/candidates/" not in route:
                    for value in ["150000", "115291", "34709", "4709", "末位考生", "MySQL 持久化"]:
                        self.assertIn(value, body)
                report["checks"].append({"path": route, "status": response.status_code})
            response = client.get(f"/runs/{run_id}/export/candidates?plan=3-H001")
            rows = list(csv.DictReader(io.StringIO(response.get_data(as_text=True).lstrip("\ufeff"))))
            _, total = repo.candidates(run_id, {"plan": "3-H001"})
            self.assertEqual(len(rows), total)
            self.assertTrue(all(r["run_id"] == run_id and r["plan_id"] == "3-H001" for r in rows))
            response = client.get(f"/runs/{run_id}/statistics.csv")
            statistics_rows = list(csv.DictReader(io.StringIO(response.get_data(as_text=True).lstrip("\ufeff"))))
            self.assertEqual(sum(int(r["candidate_count"]) for r in statistics_rows if r["dimension"] == "score"), 150000)
            self.assertEqual(sum(int(r["admitted_count"]) for r in statistics_rows if r["dimension"] == "junior"), 115291)
            report.update(status="verified", export_plan="3-H001", exported_rows=len(rows), statistics_rows=len(statistics_rows))
            save()
        except BaseException as exc:
            report.update(status="failed", error={"type": type(exc).__name__, "message": str(exc)[:300]})
            save()
            raise
        finally:
            del secret

    def test_atomic_rollback_resume_and_stale_writer(self):
        import mysql.connector
        credential_file = Path(os.environ["ADMISSION_MYSQL_TEST_CREDENTIAL_FILE"])
        secret = credential_file.read_text(encoding="utf-8-sig").strip()
        out = Path(os.environ["ADMISSION_MYSQL_TEST_OUT"])
        out.mkdir(parents=True, exist_ok=False)
        path = out / "mysql_fault_recovery.json"
        dataset_id, run_id = str(uuid4()), str(uuid4())
        report = {"status": "running", "mode": "real_mysql", "database": "admissionsim_app",
                  "created_at": datetime.now(timezone.utc).isoformat(), "source": provenance(),
                  "dataset_id": dataset_id, "run_id": run_id, "steps": {}}
        def save():
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        save()
        try:
            repo = MySQLRepository(secret)
            repo.put_dataset(sample_dataset(), "真实事务故障与恢复验收", dataset_id=dataset_id)
            repo.create_run(dataset_id, Rules(), provenance(), run_id=run_id)
            data, rules, initial = repo.load(run_id)
            first = advance(data, rules, initial)
            rows, plans = projection(data, rules, first)
            original_save = repo._save
            def fault(*args):
                original_save(*args)
                raise RuntimeError("injected_before_commit")
            with patch.object(repo, "_save", side_effect=fault):
                with self.assertRaisesRegex(RuntimeError, "injected_before_commit"):
                    Service(repo).execute(run_id)
            reconnected = MySQLRepository(secret)
            self.assertEqual(reconnected.load(run_id)[2]["completed_batch"], 0)
            reconnected.audit(run_id)
            with reconnected.transaction(readonly=True) as cur:
                cur.execute("SELECT COUNT(*) AS n FROM app_checkpoints WHERE run_id=%s", (run_id,))
                self.assertEqual(cur.fetchone()["n"], 1)
                cur.execute("SELECT COUNT(*) AS n FROM app_transfers WHERE run_id=%s", (run_id,))
                self.assertEqual(cur.fetchone()["n"], 0)
                cur.execute("SELECT COUNT(*) AS n FROM app_candidate_results WHERE run_id=%s AND admitted_batch IS NOT NULL", (run_id,))
                self.assertEqual(cur.fetchone()["n"], 0)
            report["steps"]["all_tables_rolled_back_new_connection_verified"] = True
            save()
            Service(reconnected).execute(run_id)
            after_first = MySQLRepository(secret)
            self.assertEqual(after_first.run(run_id)["completed_batch"], 1)
            Service(after_first).execute(run_id, full=True)
            self.assertEqual(after_first.run(run_id)["admitted_count"], 16)
            report["steps"]["new_service_resumed_from_persistent_batch_one"] = True
            with self.assertRaises(Conflict):
                repo.save_step(run_id, 0, first, rows, plans)
            report["steps"]["stale_writer_rejected_without_duplicate_transfer"] = True
            with self.assertRaises(mysql.connector.Error) as caught:
                with repo.transaction() as cur:
                    cur.execute("UPDATE app_plan_results SET remaining_seats=remaining_seats+1 WHERE run_id=%s AND plan_id='3-A'", (run_id,))
            self.assertEqual(caught.exception.errno, 3819)
            report["steps"]["mysql_check_constraint_rejected_bad_balance"] = True
            report["audit"] = after_first.audit(run_id)
            self.assertEqual(report["audit"]["transfers"], 4)
            report["status"] = "verified"
            save()
        except BaseException as exc:
            report.update(status="failed", error={"type": type(exc).__name__, "message": str(exc)[:300]})
            save()
            raise
        finally:
            del secret


if __name__ == "__main__":
    unittest.main()
