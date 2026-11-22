import argparse
import gc
import hashlib
import json
import platform
import sys
from datetime import datetime, timedelta, timezone
from getpass import getpass
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

import mysql.connector
import admissions_app.core.runner as core
from tools.legacy.benchmark_admissions import check_result, encode
from tools.legacy.import_scale_data import DATABASE, read_source
from admissions_app.core.second_batch import CITY_CUTOFF, GRADIENT_LINES


def file_sha(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def compare_inputs(source, candidates, plans, ranks):
    expected = sorted(source["candidates"], key=lambda c: c["id"])
    if len(candidates) != len(expected):
        raise ValueError("MySQL 读回的考生人数不一致")
    keys = ("id", "junior", "score", "tie", "choice", "second_choices", "choices")
    for actual, original in zip(candidates, expected):
        if actual != {key: original[key] for key in keys}:
            raise ValueError(f"考生输入与源文件不一致：{original['id']}")
    if plans != source["plans"]:
        raise ValueError("MySQL 读回的计划与源文件不一致")
    expected_ranks = {school: rows for school, rows in
                      source["autonomous_ranks"].items() if rows}
    if ranks != expected_ranks:
        raise ValueError("MySQL 读回的自主招生资格与源文件不一致")


def read_mysql(password, manifest, seconds):
    before = perf_counter()
    connection = mysql.connector.connect(
        host="127.0.0.1", port=3306, user="admission_app",
        password=password, database=DATABASE, charset="utf8mb4",
        autocommit=False, connection_timeout=10)
    seconds["connect"] = perf_counter() - before
    with connection:
        connection.start_transaction(
            isolation_level="REPEATABLE READ", consistent_snapshot=True, readonly=True)
        with connection.cursor() as cursor:
            before = perf_counter()
            cursor.execute(
                "SELECT dataset_id, data_sha256, manifest "
                "FROM experiment_dataset WHERE dataset_slot = 1")
            saved = cursor.fetchone()
            if saved is None or saved[:2] != (
                    manifest["dataset_id"], manifest["data_sha256"]):
                raise ValueError("数据库数据集标识或摘要与源文件不一致")
            stored = json.loads(saved[2])
            if stored["config"] != manifest["config"] or stored["counts"] != manifest["counts"]:
                raise ValueError("数据库数据集配置或计数与源文件不一致")
            cursor.execute("SELECT VERSION(), @@GLOBAL.max_allowed_packet, @@GLOBAL.log_bin")
            version, packet, log_bin = cursor.fetchone()
            server = {"version": version, "max_allowed_packet": packet, "log_bin": log_bin}
            seconds["metadata_read"] = perf_counter() - before

            print("正在从 MySQL 读取成绩、志愿、计划和资格……", flush=True)
            before = perf_counter()
            candidates, plans, ranks = core.read_admission_inputs(cursor)
            seconds["mysql_read_inputs"] = perf_counter() - before
        connection.rollback()  # 结束只读快照，随后在内存执行投档。
    return candidates, plans, ranks, server


def run_timed(candidates, plans, ranks, seconds):
    def timed(label, function):
        def wrapped(*args, **kwargs):
            key = label or f"batch_{args[0]}"
            before = perf_counter()
            value = function(*args, **kwargs)
            seconds[key] = perf_counter() - before
            return value
        return wrapped

    with patch.object(core, "validate_inputs", timed("validate_inputs", core.validate_inputs)), \
         patch.object(core, "run_first_batch", timed("batch_1", core.run_first_batch)), \
         patch.object(core, "run_second_batch", timed("batch_2", core.run_second_batch)), \
         patch.object(core, "run_general_batch", timed(None, core.run_general_batch)):
        before = perf_counter()
        result = core.run_all_batches(candidates, plans, ranks=ranks)
        seconds["admissions_total"] = perf_counter() - before
    return result


def main():
    parser = argparse.ArgumentParser(description="从 MySQL 读取 15 万人并与本地基准对照")
    parser.add_argument("--dataset", type=Path, default=Path("experiments/scale_150000"))
    parser.add_argument("--reference", type=Path, default=Path("experiments/baseline_150000_v1"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    seconds = {}
    before = perf_counter()
    source, manifest = read_source(args.dataset.resolve())
    reference = json.loads((args.reference / "benchmark.json").read_text(encoding="utf-8"))
    old_manifest = reference["dataset_manifest"]
    if any(old_manifest[key] != manifest[key] for key in ("dataset_id", "data_sha256", "config")):
        raise ValueError("所选基准与当前数据集不一致")
    config = manifest["config"]
    if (config["city_cutoff"] != CITY_CUTOFF or
            config["gradient_lines"] != list(GRADIENT_LINES) or
            config["choice_limits"] != {str(k): v for k, v in core.CHOICE_LIMITS.items()}):
        raise ValueError("当前代码规则参数与数据集不一致")
    reference_result = args.reference / "baseline_result.json"
    if file_sha(reference_result) != reference["full_result_sha256"]:
        raise ValueError("参考结果文件摘要不符")
    seconds["local_prepare"] = perf_counter() - before

    sources = {name: file_sha(name) for name in reference["source_sha256"]}
    sources.update({str(path): file_sha(path) for path in
                    (Path(__file__), Path("tools/legacy/import_scale_data.py"), Path("database/legacy/schema_scale.sql"))})
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    print("数据集：", manifest["dataset_id"])
    print("目标库：", DATABASE)
    password = getpass("请输入 admission_app 的应用密码：")
    candidates, plans, ranks, server = read_mysql(password, manifest, seconds)

    before = perf_counter()
    compare_inputs(source, candidates, plans, ranks)
    seconds["input_compare"] = perf_counter() - before
    del source
    gc.collect()
    print("投档输入与源文件一致，正在执行四批投档……", flush=True)
    result = run_timed(candidates, plans, ranks, seconds)
    before = perf_counter()
    check_result({"candidates": candidates, "plans": plans, "autonomous_ranks": ranks}, result)
    seconds["result_validate"] = perf_counter() - before

    before = perf_counter()
    expected = json.loads(reference_result.read_text(encoding="utf-8"))
    if set(result) != set(expected):
        raise ValueError("结果字段与参考结果不一致")
    for key in result:
        actual = [list(row) for row in result[key]] if key == "transfers" else result[key]
        if actual != expected[key]:
            raise ValueError(f"结果与纯 Python 基准不一致：{key}")
    del expected
    seconds["reference_compare"] = perf_counter() - before

    before = perf_counter()
    payload = encode(result)
    seconds["result_serialize"] = perf_counter() - before
    full_sha = hashlib.sha256(payload).hexdigest()
    business = {key: result[key] for key in ("admitted", "balances")}
    business["transfers"] = sorted(result["transfers"])
    business_sha = hashlib.sha256(encode(business)).hexdigest()
    if (full_sha != reference["full_result_sha256"] or
            business_sha != reference["business_sha256"]):
        raise ValueError("结果摘要与基准不一致")

    summary = {
        "candidates": len(candidates), "plans": len(plans),
        "initial_seats": sum(p["seats"] for p in plans.values()),
        "batch_admitted": {
            str(batch): sum(len(ids) for plan, ids in result["admitted"].items()
                            if plan.startswith(f"{batch}-"))
            for batch in range(1, 5)},
        "admitted": sum(map(len, result["admitted"].values())),
        "remaining_seats": sum(result["balances"].values()),
        "transfer_rows": len(result["transfers"]),
        "log_rows": len(result["first_reasons"]) + sum(
            len(result[key]) for key in ("second_log", "third_log", "fourth_log")),
    }
    if any(value != reference["summary"][key] for key, value in summary.items()):
        raise ValueError("汇总与参考基准不一致")
    if any(file_sha(path) != digest for path, digest in sources.items()):
        raise ValueError("运行期间源码发生变化")

    before = perf_counter()
    (args.out / "baseline_result.json").write_bytes(payload)
    seconds["local_result_write"] = perf_counter() - before
    report = {
        "status": "matches_baseline", "database": DATABASE,
        "created_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "dataset_id": manifest["dataset_id"], "data_sha256": manifest["data_sha256"],
        "reference": str(args.reference.resolve()), "source_sha256": sources,
        "environment": {"python": sys.version, "platform": platform.platform(),
                        "connector": mysql.connector.__version__},
        "server": server, "summary": summary, "seconds": seconds,
        "business_sha256": business_sha, "full_result_sha256": full_sha,
        "conditions": {
            "measurement_runs": 1, "database_readonly": True,
            "result_saved_to_mysql": False, "memory_measured": False,
            "scope": "MySQL 只读快照读取与内存投档；单次测量，尚未保存数据库结果",
            "cache": "未清空 MySQL 缓冲池和操作系统缓存",
            "read_timing": "四条输入查询、传输、驱动转换与 Python 输入结构构建",
        },
    }
    (args.out / "run_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nMySQL 输入与源文件一致；录取顺序、余额、结转和日志与基准完全一致")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for stage, value in seconds.items():
        print(f"{stage}: {value:.6f} 秒")
    print("业务摘要：", business_sha)
    print("完整结果摘要：", full_sha)
    print("报告目录：", args.out)


if __name__ == "__main__":
    main()