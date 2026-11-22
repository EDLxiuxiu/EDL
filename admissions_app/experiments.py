"""统一入口的必要 MySQL 查询基准；不复制投档算法，不写历史实验库。"""
import gc
import hashlib
import json
import platform
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from tools.legacy.query_candidate import build_trace, read_candidate
from tools.legacy.report_admission_run import build_report, read_run

from .domain import Rules, digest, encode, normalize, projection
from .repository import MySQLRepository, PLAN_FIELDS
from .service import ROOT, provenance

HISTORICAL_RUN = "0113c1d3-e855-42db-a16f-2578e5f3ab78"


def load_reference():
    def read(path):
        return json.loads(path.read_text(encoding="utf-8"))
    base = ROOT / "experiments"
    manifest = read(base / "scale_150000/manifest.json")
    data_path = base / "scale_150000/data.json"
    result_path = base / "mysql_run_150000_v1/baseline_result.json"
    run = read(base / "mysql_run_150000_v1/run_report.json")
    saved = read(base / "mysql_save_150000_v2/save_report.json")
    for p, expected in [(data_path, manifest["data_sha256"]), (result_path, run["full_result_sha256"])]:
        with p.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise ValueError("历史输入或结果文件摘要不一致")
    if run["status"] != "matches_baseline" or saved["status"] != "verified" or saved["run_id"] != HISTORICAL_RUN:
        raise ValueError("历史参考报告未通过验收")
    data = normalize(read(data_path))
    result = read(result_path)
    snapshot = {**data, "city_cutoff": 600}
    scores = {c["id"]: c["score"] for c in data["candidates"]}
    admitted = result["admitted"]
    ids = {cid for values in admitted.values() for cid in values}
    expected = {
        "candidate_count": len(scores), "admitted_count": len(ids),
        "initial_seats": sum(p["seats"] for p in data["plans"].values()),
        "remaining_seats": sum(result["balances"].values()), "unadmitted": sorted(scores.keys() - ids),
        "batch_counts": {b: sum(len(v) for p, v in admitted.items() if p.startswith(f"{b}-")) for b in range(1, 5)},
        "plan_rows": [{"plan_id": pid, "initial_seats": p["seats"], "admitted_count": len(admitted[pid]),
                       "remaining_seats": result["balances"][pid],
                       "lowest_score": min((scores[c] for c in admitted[pid]), default=None),
                       "last_id": admitted[pid][-1] if admitted[pid] else None,
                       "last_score": scores[admitted[pid][-1]] if admitted[pid] else None}
                      for pid, p in sorted(data["plans"].items())],
    }
    cases = read(base / "mysql_candidate_query_150000_v1/candidate_query_benchmark.json")["cases"]
    return data, result, expected, cases, manifest


def legacy_plan(cursor, run_id):
    snapshot, admitted, balances = read_run(cursor, run_id)
    result = build_report(snapshot, admitted, balances)
    del snapshot, admitted, balances
    return result


def structured_plan(cursor, run_id):
    cursor.execute("SELECT candidate_count,admitted_count,remaining_seats FROM app_runs WHERE run_id=%s", (run_id,))
    candidate_count, admitted_count, remaining = cursor.fetchone()
    cursor.execute("SELECT plan_id,initial_seats,admitted_count,remaining_seats,lowest_score,last_id,last_score "
                   "FROM app_plan_results WHERE run_id=%s ORDER BY plan_id", (run_id,))
    keys = ("plan_id", "initial_seats", "admitted_count", "remaining_seats", "lowest_score", "last_id", "last_score")
    plans = [dict(zip(keys, row)) for row in cursor]
    # 实测优化器会为 ORDER BY 选择宽主键记录；此查询固定走已验收的窄覆盖索引。
    cursor.execute("SELECT candidate_no FROM app_candidate_results FORCE INDEX (ix_candidate_batch) "
                   "WHERE run_id=%s AND admitted_batch IS NULL ORDER BY candidate_no", (run_id,))
    unadmitted = [r[0] for r in cursor]
    cursor.execute("SELECT admitted_batch,COUNT(*) FROM app_candidate_results WHERE run_id=%s AND admitted_batch IS NOT NULL GROUP BY admitted_batch", (run_id,))
    counts = {**dict.fromkeys(range(1, 5), 0), **dict(cursor)}
    return {"candidate_count": candidate_count, "admitted_count": admitted_count, "initial_seats": sum(p["initial_seats"] for p in plans),
            "remaining_seats": remaining, "unadmitted": unadmitted, "batch_counts": counts, "plan_rows": plans}


def legacy_candidate(cursor, run_id, cid):
    snapshot, candidate, logs, admission = read_candidate(cursor, run_id, cid)
    trace = build_trace(snapshot, candidate, logs, admission)
    result = {"candidate": candidate, "admission": admission, "trace": trace}
    del snapshot, logs
    return result


def structured_candidate(cursor, run_id, cid):
    cursor.execute("SELECT payload FROM app_candidate_results WHERE run_id=%s AND candidate_no=%s", (run_id, cid))
    row = json.loads(cursor.fetchone()[0])
    return {k: row[k] for k in ("candidate", "admission", "trace")}


def benchmark(password, out, *, run_id=None, repeat=3):
    if not 3 <= repeat <= 10:
        raise ValueError("正式计时次数须在 3 至 10 之间")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    report_path = out / "query_comparison.json"
    report = {"status": "preparing", "created_at": datetime.now(timezone.utc).isoformat(),
              "historical_run_id": HISTORICAL_RUN, "app_run_id": run_id, "source": provenance(),
              "environment": {"python": sys.version, "platform": platform.platform()},
              "conditions": {"warmup_rounds": 1, "timing_rounds": repeat, "readonly": True, "use_pure": True,
                             "connections": "每个数据库一个连接，全部查询复用各自的只读一致性快照；连接与预检不计时",
                             "cache": "未清 MySQL 和操作系统缓存；预检及预跑会预热缓存",
                             "scope": "全运行计划统计包含全部计划、未录名单、分批与总体汇总；考生查询包含输入、去向、四批过程",
                             "total": "SQL、传输、驱动转换、JSON解析、构建结果与释放大上下文；不是纯 SQL 时间",
                             "excluded": "本地准备、连接、预检、显式GC、结果核对、摘要、终端与文件写入",
                             "order": "有优化后运行时，逐轮交替两种实现先后顺序", "memory_measured": False},
              "samples": []}
    def save():
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    save()
    try:
        import mysql.connector
        data, original, expected, cases, manifest = load_reference()
        report["dataset_id"] = manifest["dataset_id"]
        report["data_sha256"] = manifest["data_sha256"]
        report["connector"] = mysql.connector.__version__
        if run_id:
            repo = MySQLRepository(password)
            observed_data, rules, state = repo.load(run_id)
            if digest(observed_data) != digest(data) or rules != Rules() or state["completed_batch"] != 4:
                raise ValueError("优化后运行必须使用同一 15 万人数据、相同规则，并完成四批")
            for key in original:
                if encode(state[key]) != encode(original[key]):
                    raise ValueError(f"完整业务结果或日志不一致：{key}")
            report["app_audit"] = repo.audit(run_id)
            report["business_and_full_logs_equal"] = True
        con = mysql.connector.connect(host="127.0.0.1", port=3306, user="admission_app", password=password,
                                      database="admissionsim_scale_150000", charset="utf8mb4", use_pure=True)
        connections = [con]
        try:
            con.start_transaction(isolation_level="REPEATABLE READ", consistent_snapshot=True, readonly=True)
            with con.cursor() as legacy_cur:
                legacy_cur.execute("SELECT VERSION(),@@GLOBAL.log_bin,@@GLOBAL.innodb_redo_log_capacity")
                version, log_bin, redo = legacy_cur.fetchone()
                report["server"] = {"version": version, "log_bin": log_bin, "innodb_redo_log_capacity": redo}
                implementations = [("legacy", legacy_cur, HISTORICAL_RUN)]
                app_cur = None
                if run_id:
                    new = MySQLRepository(password).connect()
                    connections.append(new)
                    new.start_transaction(isolation_level="REPEATABLE READ", consistent_snapshot=True, readonly=True)
                    app_cur = new.cursor()
                    app_cur.execute("SELECT index_name,seq_in_index,column_name FROM information_schema.statistics "
                                    "WHERE table_schema='admissionsim_app' AND table_name='app_candidate_results' "
                                    "ORDER BY index_name,seq_in_index")
                    report["app_candidate_indexes"] = app_cur.fetchall()
                    implementations.append(("structured", app_cur, run_id))
                try:
                    report["status"] = "running"
                    save()
                    for round_no in range(repeat + 1):
                        order = implementations if round_no % 2 == 0 else list(reversed(implementations))
                        for name, cursor, identifier in order:
                            jobs = [("all_plans", None, expected)]
                            if run_id:
                                jobs += [("candidate", case["candidate_no"], case["expected"]) for case in cases]
                            for kind, cid, wanted in jobs:
                                gc.collect()
                                function = (legacy_plan if name == "legacy" else structured_plan) if kind == "all_plans" else (
                                    legacy_candidate if name == "legacy" else structured_candidate)
                                before = perf_counter()
                                observed = function(cursor, identifier, cid) if cid else function(cursor, identifier)
                                seconds = perf_counter() - before
                                if encode(observed) != encode(wanted):
                                    raise ValueError(f"全量统计或考生结果与本地依据不符：{name} {cid or kind}")
                                report["samples"].append({"implementation": name, "kind": kind, "candidate_no": cid,
                                                          "round": round_no, "phase": "warmup" if round_no == 0 else "timing",
                                                          "seconds": seconds, "result_sha256": digest(observed)})
                                del observed
                                save()
                                print(f"{name} 第 {round_no} 轮 {cid or '全部 48250 条计划'}：{seconds:.6f} 秒", flush=True)
                finally:
                    if app_cur:
                        app_cur.close()
        finally:
            for connection in connections:
                try:
                    connection.rollback()
                finally:
                    connection.close()
        groups = {}
        for row in report["samples"]:
            if row["phase"] == "timing":
                key = (row["implementation"], row["kind"], row["candidate_no"])
                groups.setdefault(key, []).append(row["seconds"])
        report["aggregates"] = [{"implementation": k[0], "kind": k[1], "candidate_no": k[2],
                                  "min": min(v), "median": statistics.median(v), "max": max(v)} for k, v in groups.items()]
        report["verified_query_count"] = len(report["samples"])
        if report["source"]["source_sha256"] != provenance()["source_sha256"]:
            raise ValueError("计时期间源码发生变化，保留本次样本但不标记通过")
        report["status"] = "verified"
        save()
    except BaseException as exc:
        report.update(status="failed", error={"type": type(exc).__name__, "message": str(exc)[:300]})
        save()
        raise
    print("全量核对通过。报告：", report_path)


def acceptance(password, out, *, resume=False):
    """新主库真实验收：输入一次应用密码。固定 UUID 可在提交响应丢失后恢复。"""
    from .domain import run_complete, sample_dataset
    from .service import Service
    out = Path(out)
    report_path = out / "acceptance.json"
    if resume:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report["source"]["source_sha256"] != provenance()["source_sha256"]:
            raise ValueError("源码已变化，请先核对旧报告，不自动恢复该次验收")
    else:
        out.mkdir(parents=True, exist_ok=False)
        report = {"status": "preparing", "mode": "real_mysql", "database": "admissionsim_app",
                  "source": provenance(), "created_at": datetime.now(timezone.utc).isoformat(), "stages": {},
                  "ids": {k: str(uuid4()) for k in ("sample_dataset", "sample_run", "scale_dataset", "scale_run")}}
    def save():
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    def stage(name, function):
        report["phase"] = name
        report["status"] = "running"
        save()
        before = perf_counter()
        value = function()
        report["stages"][name] = {"status": "verified", "seconds": perf_counter() - before}
        save()
        return value
    save()
    try:
        data, reference, expected, cases, manifest = load_reference()
        repo = MySQLRepository(password)
        ids = report["ids"]
        print("目标库 admissionsim_app；固定标识：", json.dumps(ids), flush=True)
        for prefix, inputs in (("sample", sample_dataset()), ("scale", data)):
            dataset_id, run_id = ids[prefix + "_dataset"], ids[prefix + "_run"]
            def prepare_dataset():
                try:
                    existing = repo.dataset(dataset_id)
                except ValueError as exc:
                    if str(exc) != "数据版本不存在":
                        raise
                    repo.put_dataset(inputs, "主系统真实验收 · " + prefix, dataset_id=dataset_id)
                else:
                    if digest(existing) != digest(inputs):
                        raise ValueError("固定版本编号对应的输入不同，停止恢复")
            stage(prefix + "_dataset", prepare_dataset)
            def prepare_run():
                try:
                    meta = repo.run(run_id)
                except ValueError as exc:
                    if str(exc) != "运行不存在":
                        raise
                    repo.create_run(dataset_id, Rules(), provenance(), run_id=run_id)
                else:
                    if meta["dataset_id"] != dataset_id or meta["rules"] != vars(Rules()):
                        raise ValueError("固定运行的输入或规则不同")
            stage(prefix + "_create_run", prepare_run)
            def execute():
                if repo.run(run_id)["completed_batch"] < 4:
                    Service(repo).execute(run_id, full=True, progress=lambda text: print(prefix, text, flush=True))
            stage(prefix + "_execute", execute)
            report[prefix + "_audit"] = stage(prefix + "_audit", lambda: repo.audit(run_id))
            actual = repo.load(run_id)[2]
            wanted = reference if prefix == "scale" else run_complete(inputs, Rules())
            for key in wanted:
                if key != "completed_batch" and encode(actual[key]) != encode(wanted[key]):
                    raise ValueError(f"{prefix} 完整结果对照失败：{key}")
            report[prefix + "_full_result_equal"] = True
            save()
        query_dir = out / "queries"
        if query_dir.exists():
            previous = json.loads((query_dir / "query_comparison.json").read_text(encoding="utf-8"))
            if previous["status"] != "verified" or previous["app_run_id"] != ids["scale_run"]:
                raise ValueError("查询目录已有未通过报告，请保留证据，另用 benchmark 命令指定新目录补测")
        else:
            stage("query_comparison", lambda: benchmark(password, query_dir, run_id=ids["scale_run"]))
        report["status"] = "verified"
        save()
    except BaseException as exc:
        report.update(status="needs_attention", error={"type": type(exc).__name__, "message": str(exc)[:300]})
        save()
        print("验收未完成；保留同一目录和 UUID，不删除、不盲目重建。报告：", report_path, flush=True)
        raise
    print("主系统真实 MySQL 验收完成。报告：", report_path)


def verify_local(out):
    """新实现本地验收，明确不连接 MySQL，不替代数据库实测。"""
    from .domain import run_complete, verify_state
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    path = out / "local_verification.json"
    report = {"status": "running", "mode": "local_python_and_memory", "mysql_connected": False,
              "created_at": datetime.now(timezone.utc).isoformat(), "source": provenance()}
    def save():
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    save()
    try:
        completed = subprocess.run([sys.executable, "-B", "-m", "unittest", "-v", "tests.test_admissions", "tests.test_input_validation", "tests.test_system"],
                                   cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
        (out / "unittest.txt").write_text(completed.stdout + completed.stderr, encoding="utf-8")
        report["unittest_exit_code"] = completed.returncode
        if completed.returncode:
            raise ValueError("自动化测试未通过，见 unittest.txt")
        data, original, expected, cases, manifest = load_reference()
        before = perf_counter()
        state = run_complete(data, Rules())
        report["new_implementation_single_run_seconds"] = perf_counter() - before
        report["timing_note"] = "新实现单次本地正确性验收，不作为 MySQL 或同口径优化收益数据"
        verify_state(data, Rules(), state)
        for key in original:
            if encode(original[key]) != encode(state[key]):
                raise ValueError(f"15 万人完整结果与已验收历史文件不一致：{key}")
        rows, plans = projection(data, Rules(), state)
        indexed = {r["candidate"]["id"]: r for r in rows}
        for case in cases:
            selected = {k: indexed[case["candidate_no"]][k] for k in ("candidate", "admission", "trace")}
            if encode(selected) != encode(case["expected"]):
                raise ValueError("固定六类考生处理过程与历史查询样本不一致")
        old_plan_fields = expected["plan_rows"][0].keys()
        if [{k: p[k] for k in old_plan_fields} for p in plans] != expected["plan_rows"]:
            raise ValueError("全量计划统计与独立本地预期不一致")
        report.update(status="verified", dataset_id=manifest["dataset_id"], candidates=len(rows), plans=len(plans),
                      admitted=sum(p["admitted_count"] for p in plans), remaining_seats=sum(state["balances"].values()),
                      transfers=len(state["transfers"]), fixed_candidate_cases=len(cases),
                      business_and_full_logs_equal=True,
                      full_result_sha256=digest({k: state[k] for k in original}))
        save()
    except BaseException as exc:
        report.update(status="failed", error={"type": type(exc).__name__, "message": str(exc)[:300]})
        save()
        raise
    print("本地 15 万人新实现核对通过；未连接 MySQL。报告：", path)
