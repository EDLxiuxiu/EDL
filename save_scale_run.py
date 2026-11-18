import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from getpass import getpass
from itertools import zip_longest
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

import mysql.connector
from mysql.connector.conversion import MySQLConverter

from benchmark_admissions import check_result, encode
from import_scale_data import DATABASE, read_source, verify_database
from run_admissions import CHOICE_LIMITS
from second_batch import CITY_CUTOFF, GRADIENT_LINES

LOG_KEYS = ("first_reasons", "second_log", "third_log", "fourth_log")
RUN_SQL = (
    "INSERT INTO admission_runs "
    "(run_id, candidate_count, admitted_count, input_snapshot, decision_log) "
    "VALUES (%s, %s, %s, %s, %s)"
)
TABLE_SQL = (
    "INSERT INTO run_plan_balances (run_id, plan_id, remaining_seats) "
    "VALUES (%s, %s, %s)",
    "INSERT INTO run_admission_details "
    "(run_id, candidate_no, plan_id, admission_order) VALUES (%s, %s, %s, %s)",
    "INSERT INTO run_seat_transfers "
    "(run_id, transfer_no, source_plan_id, target_plan_id, seats) "
    "VALUES (%s, %s, %s, %s, %s)",
)
CHECK_SQL = (
    "SELECT run_id, plan_id, remaining_seats FROM run_plan_balances "
    "WHERE run_id = %s ORDER BY plan_id",
    "SELECT run_id, candidate_no, plan_id, admission_order FROM run_admission_details "
    "WHERE run_id = %s ORDER BY plan_id, admission_order",
    "SELECT run_id, transfer_no, source_plan_id, target_plan_id, seats "
    "FROM run_seat_transfers WHERE run_id = %s ORDER BY transfer_no",
)
LABELS = ("balances", "details", "transfers")


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    temporary.replace(path)


def connect(password):
    return mysql.connector.connect(
        host="127.0.0.1", port=3306, user="admission_app", password=password,
        database=DATABASE, charset="utf8mb4", use_pure=True,
        autocommit=False, connection_timeout=10)


def timed(seconds, key, function, *args):
    before = perf_counter()
    value = function(*args)
    seconds[key] = perf_counter() - before
    return value


def load_local(paths):
    data, manifest = read_source(Path(paths["dataset"]))
    run_path = Path(paths["mysql_run"]) / "run_report.json"
    run = read_json(run_path)
    base = read_json(Path(paths["reference"]) / "benchmark.json")
    if run["status"] != "matches_baseline" or run["database"] != DATABASE:
        raise ValueError("MySQL 投档报告状态或目标库不符")
    for key in ("dataset_id", "data_sha256"):
        if not (manifest[key] == run[key] == base["dataset_manifest"][key]):
            raise ValueError("数据集标识或源文件摘要不一致")
    config = manifest["config"]
    if (config["city_cutoff"] != CITY_CUTOFF or
            config["gradient_lines"] != list(GRADIENT_LINES) or
            config["choice_limits"] != {str(k): v for k, v in CHOICE_LIMITS.items()}):
        raise ValueError("当前规则参数与数据集不一致")
    sources = dict(base["source_sha256"])
    sources.update(run["source_sha256"])
    for name, digest in sources.items():
        if sha(name) != digest:
            raise ValueError(f"源码与历史报告不一致：{Path(name).name}")
    sources[str(Path(__file__).resolve())] = sha(__file__)
    result_path = Path(paths["mysql_run"]) / "baseline_result.json"
    base_path = Path(paths["reference"]) / "baseline_result.json"
    full_sha = sha(result_path)
    if not (full_sha == sha(base_path) == run["full_result_sha256"] ==
            base["full_result_sha256"]):
        raise ValueError("完整结果文件摘要不一致")
    result = read_json(result_path)
    if result != read_json(base_path):
        raise ValueError("两份完整结果逐字段比较失败")
    if (set(result["admitted"]) != set(data["plans"]) or
            set(result["balances"]) != set(data["plans"])):
        raise ValueError("结果中的计划集合不完整")
    check_result(data, result)
    business = {key: result[key] for key in ("admitted", "balances")}
    business["transfers"] = sorted(result["transfers"])
    business_sha = hashlib.sha256(encode(business)).hexdigest()
    if business_sha != run["business_sha256"] or business_sha != base["business_sha256"]:
        raise ValueError("业务结果摘要不一致")
    summary = {
        "candidates": len(data["candidates"]), "plans": len(data["plans"]),
        "initial_seats": sum(p["seats"] for p in data["plans"].values()),
        "batch_admitted": {
            str(b): sum(len(ids) for p, ids in result["admitted"].items()
                        if p.startswith(f"{b}-")) for b in range(1, 5)},
        "admitted": sum(map(len, result["admitted"].values())),
        "remaining_seats": sum(result["balances"].values()),
        "transfer_rows": len(result["transfers"]),
        "log_rows": len(result["first_reasons"]) +
                    sum(len(result[k]) for k in LOG_KEYS[1:]),
    }
    if summary != run["summary"] or any(base["summary"][k] != v for k, v in summary.items()):
        raise ValueError("汇总与历史报告不一致")
    provenance = {
        "database": DATABASE, "dataset_manifest": manifest,
        "mysql_run_report_sha256": sha(run_path),
        "full_result_sha256": full_sha, "business_sha256": business_sha,
        "source_sha256": sources,
    }
    snapshot = {
        **data, "city_cutoff": CITY_CUTOFF,
        "gradient_lines": list(GRADIENT_LINES), "choice_limits": CHOICE_LIMITS,
        "provenance": provenance,
    }
    return data, manifest, result, summary, snapshot, provenance


def serialize(snapshot, result):
    logs = {key: result[key] for key in LOG_KEYS}
    return tuple(json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                 for value in (snapshot, logs))


def build_rows(run_id, result):
    return (
        [(run_id, p, n) for p, n in sorted(result["balances"].items())],
        [(run_id, c, p, i) for p, ids in sorted(result["admitted"].items())
         for i, c in enumerate(ids, 1)],
        [(run_id, i, *row) for i, row in enumerate(result["transfers"], 1)],
    )


def command_bytes(sql, params, converter, sql_mode):
    # 与 use_pure=True 的转换、转义和引用规则一致，包含 COM_QUERY 字节。
    return len(sql.encode("utf-8")) - 2 * len(params) + 1 + sum(
        len(converter.quote(converter.escape(converter.to_mysql(v), sql_mode)))
        for v in params)


def write_chunks(cursor, sql, rows, size):
    for start in range(0, len(rows), size):
        cursor.executemany(sql, rows[start:start + size])


def compare_rows(cursor, sql, run_id, expected):
    cursor.execute(sql, (run_id,))
    mismatch = False
    for actual, wanted in zip_longest(cursor, expected):
        if actual != wanted:
            mismatch = True
    if mismatch:
        raise ValueError("数据库逐行查回比较失败")


def check_saved(password, run_id, summary, payload, rows, seconds):
    connection = timed(seconds, "check_connect", connect, password)
    with connection:
        connection.start_transaction(isolation_level="REPEATABLE READ",
                                     consistent_snapshot=True, readonly=True)
        with connection.cursor() as cursor:
            before = perf_counter()
            cursor.execute(
                "SELECT candidate_count, admitted_count, input_snapshot, decision_log "
                "FROM admission_runs WHERE run_id = %s", (run_id,))
            saved = cursor.fetchone()
            seconds["check_header_read"] = perf_counter() - before
            if saved is None:
                raise ValueError("当前快照未找到 run_id；写入进程若仍在运行，请勿重试保存")
            before = perf_counter()
            if saved[:2] != (summary["candidates"], summary["admitted"]):
                raise ValueError("查回人数不一致")
            if any(json.loads(saved[i + 2]) != json.loads(payload[i]) for i in range(2)):
                raise ValueError("完整输入快照或全部处理日志不一致")
            seconds["check_snapshot_and_logs"] = perf_counter() - before
            for label, sql, expected in zip(LABELS, CHECK_SQL, rows):
                timed(seconds, f"check_{label}", compare_rows,
                      cursor, sql, run_id, expected)
        connection.rollback()


def main():
    parser = argparse.ArgumentParser(description="保存已验收的15万人结果并完整查回核对")
    parser.add_argument("--dataset", type=Path, default=Path("experiments/scale_150000"))
    parser.add_argument("--mysql-run", type=Path, default=Path("experiments/mysql_run_150000_v1"))
    parser.add_argument("--reference", type=Path, default=Path("experiments/baseline_150000_v1"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--check", action="store_true", help="按已有报告的run_id只读检查")
    args = parser.parse_args()
    if not 100 <= args.batch_size <= 5000:
        parser.error("--batch-size 应在100至5000之间")
    out = args.out.resolve()
    report_path = out / "save_report.json"
    if args.check:
        report = read_json(report_path)
        if report["database"] != DATABASE:
            raise ValueError("检查报告的目标库不符")
        run_id = str(UUID(report["run_id"]))
        paths = report["paths"]
    else:
        out.mkdir(parents=True, exist_ok=False)
        run_id = str(uuid4())
        paths = {k: str(getattr(args, k).resolve())
                 for k in ("dataset", "mysql_run", "reference")}
        report = {
            "status": "preparing", "run_id": run_id, "database": DATABASE,
            "paths": paths, "batch_size": args.batch_size,
            "started_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
            "connector": mysql.connector.__version__, "python": sys.version, "seconds": {},
            "conditions": "保存已验收结果；完整输入快照和日志；单事务按批发送；"
                          "单次测量，未清缓存，未测内存；use_pure=True",
        }
        write_json(report_path, report)
    print("目标库：", DATABASE, flush=True)
    print("运行 UUID：", run_id, flush=True)
    seconds = {} if args.check else report["seconds"]
    connection = None
    commit_attempted = committed = False
    try:
        data, manifest, result, summary, snapshot, provenance = timed(
            seconds, "local_validate", load_local, paths)
        payload = timed(seconds, "json_serialize", serialize, snapshot, result)
        rows = timed(seconds, "build_rows", build_rows, run_id, result)
        digests = [hashlib.sha256(v.encode("utf-8")).hexdigest() for v in payload]
        if args.check:
            if report.get("payload_sha256") != digests:
                raise ValueError("本地快照或日志与保存报告不一致")
        else:
            report.update(summary=summary, provenance=provenance,
                          payload_sha256=digests, status="prepared")
            write_json(report_path, report)
        password = getpass("请输入 admission_app 的应用密码：")
        if args.check:
            timed(seconds, "check_total", check_saved,
                  password, run_id, summary, payload, rows, seconds)
            check_path = out / ("check_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".json")
            write_json(check_path, {"status": "verified", "run_id": run_id,
                                   "database": DATABASE, "seconds": seconds})
            print("只读查回核对通过；未写入数据库")
            print("检查报告：", check_path)
            return
        connection = timed(seconds, "connect", connect, password)
        connection.start_transaction(isolation_level="REPEATABLE READ", consistent_snapshot=True)
        with connection.cursor() as cursor:
            before = perf_counter()
            verify_database(cursor, data, manifest, full=False)
            cursor.execute("SELECT VERSION(), @@SESSION.max_allowed_packet, "
                           "@@GLOBAL.log_bin, @@SESSION.sql_mode")
            version, packet, log_bin, sql_mode = cursor.fetchone()
            report["server"] = {"version": version, "max_allowed_packet": packet,
                                "log_bin": log_bin, "sql_mode": sql_mode}
            cursor.execute("SELECT run_id FROM admission_runs WHERE run_id = %s", (run_id,))
            if cursor.fetchone() is not None:
                raise ValueError("run_id 已存在，拒绝重复保存")
            seconds["precheck"] = perf_counter() - before
            params = (run_id, summary["candidates"], summary["admitted"], *payload)
            before = perf_counter()
            converter = MySQLConverter(charset="utf8mb4")
            size = command_bytes(RUN_SQL, params, converter, sql_mode)
            report["main_command_bytes"] = size
            if size >= packet - 1024**2:
                raise ValueError("主INSERT距max_allowed_packet不足1 MiB，停止写入")
            for sql, group in zip(TABLE_SQL, rows):
                widest = max((command_bytes(sql, row, converter, sql_mode)
                              for row in group), default=0)
                if widest * args.batch_size >= packet - 1024**2:
                    raise ValueError("分批语句上界过大，请减小batch-size")
            seconds["packet_check"] = perf_counter() - before
            print(f"主INSERT命令 {size / 1024**2:.2f} MiB；"
                  f"会话上限 {packet / 1024**2:.2f} MiB", flush=True)
            report["status"] = "writing"
            write_json(report_path, report)
            timed(seconds, "insert_run", cursor.execute, RUN_SQL, params)
            for label, sql, group in zip(LABELS, TABLE_SQL, rows):
                print(f"正在发送 {label}：{len(group)} 行（尚未提交）", flush=True)
                timed(seconds, f"insert_{label}", write_chunks,
                      cursor, sql, group, args.batch_size)
        report["status"] = "commit_pending"
        write_json(report_path, report)
        print("全部发送完成，正在提交唯一事务……", flush=True)
        commit_attempted = True
        timed(seconds, "commit", connection.commit)
        committed = True
        report["status"] = "committed"
        write_json(report_path, report)
        connection.close()
        connection = None
        timed(seconds, "post_commit_total", check_saved,
              password, run_id, summary, payload, rows, seconds)
        seconds["sql_write_total"] = sum(seconds["insert_" + key]
                                         for key in ("run", *LABELS))
        seconds["write_and_commit"] = seconds["sql_write_total"] + seconds["commit"]
        report["status"] = "verified"
        write_json(report_path, report)
    except BaseException:
        if connection is not None and not committed:
            try:
                connection.rollback()
            except Exception:
                pass
        if not args.check:
            report["status"] = ("committed_check_failed" if committed else
                                "commit_unknown" if commit_attempted else "not_committed")
            write_json(report_path, report)
            print("本次状态：", report["status"], flush=True)
            if commit_attempted:
                print("请用相同--out加--check查回；不要重新执行保存。", flush=True)
        raise
    finally:
        if connection is not None:
            connection.close()
    print("保存成功：提交后新连接完整核对通过")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for key, value in seconds.items():
        print(f"{key}: {value:.6f} 秒")
    print("报告：", report_path)


if __name__ == "__main__":
    main()