import argparse
import gc
import hashlib
import json
import platform
import statistics
import sys
from datetime import datetime, timedelta, timezone
from getpass import getpass
from pathlib import Path
from time import perf_counter
from uuid import UUID

import mysql.connector

from tools.legacy.benchmark_admissions import encode
from tools.legacy.query_candidate import build_trace, read_candidate
from tools.legacy.save_scale_run import (
    DATABASE, LOG_KEYS, connect, load_local, read_json, serialize, sha, write_json,
)

STAGES = ("read_decode", "build_trace", "release_context", "total")


def prepare_cases(saved):
    loaded = load_local(saved["paths"])
    data, manifest, result, summary, snapshot, provenance = loaded
    if summary != saved["summary"] or provenance != saved["provenance"]:
        raise ValueError("本地数据或结果与已保存运行的报告不一致")
    payload = serialize(snapshot, result)
    digests = [hashlib.sha256(value.encode("utf-8")).hexdigest() for value in payload]
    if digests != saved["payload_sha256"]:
        raise ValueError("完整快照或日志摘要与保存报告不一致")
    del payload
    admissions = {
        candidate_no: (plan_id, order)
        for plan_id, ids in result["admitted"].items()
        for order, candidate_no in enumerate(ids, 1)
    }
    by_id = {c["id"]: c for c in data["candidates"]}
    groups = []
    for batch in range(1, 5):
        ids = [c for c, admission in admissions.items()
               if admission[0].startswith(f"{batch}-")]
        groups.append((f"admitted_batch_{batch}", ids, batch - 1, 3))

    def has_choices(c):
        return bool(c["choice"] or c["second_choices"] or
                    c["choices"][3] or c["choices"][4])

    for label, above in (("unadmitted_above_cutoff", True),
                         ("unadmitted_below_cutoff", False)):
        ids = [
            c["id"] for c in data["candidates"]
            if c["id"] not in admissions and has_choices(c)
            and (c["score"] >= snapshot["city_cutoff"]) == above
        ]
        groups.append((label, ids, 1, 2))
    logs = {key: result[key] for key in LOG_KEYS}
    cases = []
    for label, ids, numerator, denominator in groups:
        ids.sort()
        if not ids:
            raise ValueError(f"没有符合条件的查询样本：{label}")
        candidate_no = ids[(len(ids) - 1) * numerator // denominator]
        candidate = by_id[candidate_no]
        admission = admissions.get(candidate_no)
        expected = json.loads(encode({
            "candidate": candidate,
            "admission": admission,
            "trace": build_trace(snapshot, candidate, logs, admission),
        }))
        cases.append({
            "label": label, "candidate_no": candidate_no,
            "expected": expected,
            "expected_sha256": hashlib.sha256(encode(expected)).hexdigest(),
        })
    return cases


def query_once(cursor, run_id, candidate_no):
    before = perf_counter()
    snapshot, candidate, logs, admission = read_candidate(cursor, run_id, candidate_no)
    after_read = perf_counter()
    trace = build_trace(snapshot, candidate, logs, admission)
    after_trace = perf_counter()
    observed = {"candidate": candidate, "admission": admission, "trace": trace}
    del snapshot, logs
    finished = perf_counter()
    return observed, {
        "read_decode": after_read - before,
        "build_trace": after_trace - after_read,
        "release_context": finished - after_trace,
        "total": finished - before,
    }


def measure(cursor, run_id, case):
    gc.collect()  # 显式回收在计时范围外；查询过程中仍保持GC启用。
    observed, seconds = query_once(cursor, run_id, case["candidate_no"])
    actual = encode(observed)
    if actual != encode(case["expected"]):
        raise ValueError(f"查询结果与本地已验收结果不一致：{case['candidate_no']}")
    return {
        "label": case["label"], "candidate_no": case["candidate_no"],
        "seconds": seconds, "result_sha256": hashlib.sha256(actual).hexdigest(),
    }


def aggregate(cases, samples):
    rows = []
    for case in cases:
        values = [row for row in samples if row["candidate_no"] == case["candidate_no"]]
        row = {"label": case["label"], "candidate_no": case["candidate_no"], "seconds": {}}
        for stage in STAGES:
            timings = [sample["seconds"][stage] for sample in values]
            row["seconds"][stage] = {
                "min": min(timings), "median": statistics.median(timings),
                "max": max(timings),
            }
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description="15万人运行的现有考生查询性能基准")
    parser.add_argument(
        "--save-report", type=Path,
        default=Path("experiments/mysql_save_150000_v2/save_report.json"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.repeat <= 10:
        parser.error("--repeat应在1至10之间")
    args.out.mkdir(parents=True, exist_ok=False)
    report_path = args.out / "candidate_query_benchmark.json"
    report = {
        "status": "preparing", "database": DATABASE,
        "created_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "save_report": str(args.save_report.resolve()),
        "environment": {
            "python": sys.version, "platform": platform.platform(),
            "connector": mysql.connector.__version__, "use_pure": True,
        },
        "conditions": {
            "readonly": True, "warmup_rounds": 1, "timing_rounds": args.repeat,
            "case_count": 6, "fresh_process_each_query": False,
            "connection": "所有查询复用一个连接、一个只读一致性快照",
            "cache": "未清MySQL及操作系统缓存；元数据预检及预跑会预热缓存",
            "memory_measured": False,
            "scope": "复用read_candidate和build_trace；每次重新读取整次快照和日志",
            "read_decode": "两条SQL、传输、驱动转换、JSON解析及查找考生",
            "total": "读取解析、构建处理过程、释放整次查询上下文",
            "excluded": "连接、事务启动、预检、本地准备、显式GC、结果核对、输出及报告写入",
            "selection": "各批已录取名单按考生号排序，依次取首位、1/3、2/3、末位；"
                         "两类有志愿未录取名单按考生号排序取中位位置",
        },
        "warmup_samples": [], "timing_samples": [],
    }
    write_json(report_path, report)
    try:
        before = perf_counter()
        saved = read_json(args.save_report)
        if saved["status"] != "verified" or saved["database"] != DATABASE:
            raise ValueError("必须使用实验库已完整查回验收通过的保存报告")
        run_id = str(UUID(saved["run_id"]))
        cases = prepare_cases(saved)
        sources = dict(saved["provenance"]["source_sha256"])
        for name in ("tools/legacy/query_candidate.py", str(Path(__file__).resolve())):
            sources[name] = sha(name)
        if any(sha(name) != digest for name, digest in sources.items()):
            raise ValueError("当前源码与实验记录不一致")
        files = {
            str(args.save_report.resolve()): sha(args.save_report),
            str(Path(saved["paths"]["dataset"]) / "data.json"):
                saved["provenance"]["dataset_manifest"]["data_sha256"],
            str(Path(saved["paths"]["mysql_run"]) / "baseline_result.json"):
                saved["provenance"]["full_result_sha256"],
        }
        report.update(
            run_id=run_id, dataset_id=saved["provenance"]["dataset_manifest"]["dataset_id"],
            business_sha256=saved["provenance"]["business_sha256"],
            full_result_sha256=saved["provenance"]["full_result_sha256"],
            cases=cases, source_sha256=sources, file_sha256=files,
            local_prepare_seconds=perf_counter() - before, status="prepared")
        write_json(report_path, report)
        gc.collect()
        print("目标库：", DATABASE)
        print("运行编号：", run_id)
        for case in cases:
            expected = case["expected"]
            print(case["label"], case["candidate_no"],
                  f"分数{expected['candidate']['score']}",
                  expected["admission"] or "未录取")
        password = getpass("请输入 admission_app 的应用密码：")
        before = perf_counter()
        with connect(password) as connection:
            report["connect_seconds"] = perf_counter() - before
            connection.start_transaction(
                isolation_level="REPEATABLE READ", consistent_snapshot=True, readonly=True)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT DATABASE(), VERSION(), @@SESSION.max_allowed_packet, "
                    "@@GLOBAL.log_bin")
                database, version, packet, log_bin = cursor.fetchone()
                if database != DATABASE:
                    raise ValueError("实际连接库不符")
                report["server"] = {
                    "version": version, "max_allowed_packet": packet, "log_bin": log_bin}
                cursor.execute(
                    "SELECT candidate_count, admitted_count FROM admission_runs "
                    "WHERE run_id = %s", (run_id,))
                if cursor.fetchone() != (
                        saved["summary"]["candidates"], saved["summary"]["admitted"]):
                    raise ValueError("数据库运行记录不存在或人数不一致")
                report["status"] = "running"
                for round_no in range(args.repeat + 1):
                    phase = "warmup" if round_no == 0 else "timing"
                    for case in cases:
                        report["progress"] = {
                            "phase": phase, "round": round_no,
                            "candidate_no": case["candidate_no"]}
                        write_json(report_path, report)
                        print(f"{phase} {round_no}：{case['candidate_no']}", flush=True)
                        sample = measure(cursor, run_id, case)
                        sample["round"] = round_no
                        key = "warmup_samples" if round_no == 0 else "timing_samples"
                        report[key].append(sample)
                    write_json(report_path, report)
            connection.rollback()
        if any(sha(name) != digest for name, digest in {**sources, **files}.items()):
            raise ValueError("测量期间源码、输入、结果或保存报告发生变化")
        report["aggregates"] = aggregate(cases, report["timing_samples"])
        report["verified_query_count"] = len(report["warmup_samples"]) + len(report["timing_samples"])
        report["status"] = "verified"
        write_json(report_path, report)
    except BaseException as exc:
        report["status"] = "failed"
        report["error"] = {
            "type": type(exc).__name__, "errno": getattr(exc, "errno", None),
            "message": str(exc)[:500],
        }
        write_json(report_path, report)
        raise SystemExit(f"查询基准未完成；详情见 {report_path}") from None
    print("\n查询结果核对通过，各阶段中位数（秒）：")
    for row in report["aggregates"]:
        seconds = row["seconds"]
        print(f"{row['candidate_no']} {row['label']} "
              f"读取解析={seconds['read_decode']['median']:.6f} "
              f"处理过程={seconds['build_trace']['median']:.6f} "
              f"总计={seconds['total']['median']:.6f}")
    print("已核对查询次数：", report["verified_query_count"])
    print("报告：", report_path)


if __name__ == "__main__":
    main()