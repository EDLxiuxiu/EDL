import argparse
import hashlib
from datetime import datetime, timedelta, timezone
from getpass import getpass
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from save_scale_run import (
    DATABASE, RUN_SQL, command_bytes, connect, load_local,
    read_json, serialize, sha, write_json,
)

TABLES = (
    "admission_runs", "run_plan_balances",
    "run_admission_details", "run_seat_transfers",
)


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def mark(report, path, phase):
    report["phase"] = phase
    report["phase_started_at"] = now()
    write_json(path, report)


def counts_for(cursor, run_id):
    counts = {}
    for table in TABLES:
        cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE run_id = %s", (run_id,))
        counts[table] = cursor.fetchone()[0]
    return counts


def main():
    parser = argparse.ArgumentParser(description="单条运行主记录写入、回滚及新连接查无记录诊断")
    parser.add_argument(
        "--save-report", type=Path,
        default=Path("experiments/mysql_save_150000_v1/save_report.json"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    path = args.out / "write_probe_report.json"
    run_id = str(uuid4())
    report = {
        "status": "preparing", "diagnostic_run_id": run_id, "database": DATABASE,
        "started_at": now(), "save_report": str(args.save_report.resolve()),
        "source_sha256": {
            "probe": sha(__file__), "save_scale_run.py": sha("save_scale_run.py")},
        "conditions": {
            "scope": "只插入一条admission_runs，随后回滚并用新连接查无记录",
            "measurement_runs": 1, "commit_called": False,
            "driver": "use_pure=True；复用原连接函数",
        },
        "seconds": {},
    }
    mark(report, path, "local_prepare")
    connection = None
    insert_started = None
    print("目标库：", DATABASE, flush=True)
    print("本次诊断 UUID：", run_id, flush=True)
    try:
        before = perf_counter()
        saved = read_json(args.save_report)
        if saved["database"] != DATABASE:
            raise ValueError("原报告目标库不符")
        loaded = load_local(saved["paths"])
        summary = loaded[3]
        payload = serialize(loaded[4], loaded[2])
        del loaded
        digests = [hashlib.sha256(v.encode("utf-8")).hexdigest() for v in payload]
        if digests != saved["payload_sha256"]:
            raise ValueError("两份JSON文本与原保存报告不一致")
        report.update(source_run_id=saved["run_id"], payload_sha256=digests)
        params = (run_id, summary["candidates"], summary["admitted"], *payload)
        report["seconds"]["local_prepare"] = perf_counter() - before
        mark(report, path, "connect")
        password = getpass("请输入 admission_app 的应用密码：")
        connection = connect(password)
        connection.start_transaction(isolation_level="REPEATABLE READ")
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT DATABASE(), VERSION(), @@SESSION.max_allowed_packet, "
                "@@SESSION.sql_mode, @@GLOBAL.log_bin, @@SESSION.binlog_format, "
                "@@GLOBAL.binlog_cache_size, @@GLOBAL.max_binlog_cache_size, "
                "@@GLOBAL.tmpdir")
            values = cursor.fetchone()
            if values[0] != DATABASE:
                raise ValueError("实际连接库不符")
            report["server"] = dict(zip(
                ("database", "version", "max_allowed_packet", "sql_mode", "log_bin",
                 "binlog_format", "binlog_cache_size", "max_binlog_cache_size", "tmpdir"),
                values))
            cursor.execute(
                "SELECT table_name, engine FROM information_schema.tables "
                "WHERE table_schema = %s", (DATABASE,))
            engines = dict(cursor)
            if any(engines.get(t, "").upper() != "INNODB" for t in TABLES):
                raise ValueError("结果表缺失或不是InnoDB，停止写入诊断")
            report["counts_before"] = counts_for(cursor, run_id)
            if any(report["counts_before"].values()):
                raise ValueError("诊断UUID已存在，停止操作")
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Uptime'")
            report["uptime_before"] = int(cursor.fetchone()[1])
            size = command_bytes(RUN_SQL, params, connection.converter, values[3])
            report["command_bytes"] = size
            if size >= values[2] - 1024**2:
                raise ValueError("主INSERT距会话发送上限不足1 MiB")
            report["status"] = "running"
            mark(report, path, "insert_run")
            print(f"即将执行主INSERT：{size / 1024**2:.2f} MiB；完成后回滚", flush=True)
            insert_started = perf_counter()
            cursor.execute(RUN_SQL, params)
            report["seconds"]["insert_run"] = perf_counter() - insert_started
            insert_started = None
            report["affected_rows"] = cursor.rowcount
            mark(report, path, "read_uncommitted_row")
            if cursor.rowcount != 1:
                raise ValueError("主记录影响行数不为1")
            cursor.execute(
                "SELECT candidate_count, admitted_count, "
                "JSON_STORAGE_SIZE(input_snapshot), JSON_STORAGE_SIZE(decision_log) "
                "FROM admission_runs WHERE run_id = %s", (run_id,))
            actual = cursor.fetchone()
            report["row_before_rollback"] = list(actual) if actual is not None else None
            if actual is None or actual[:2] != (summary["candidates"], summary["admitted"]):
                raise ValueError("本事务内读取主记录失败")
        mark(report, path, "rollback")
        before = perf_counter()
        connection.rollback()
        report["seconds"]["rollback"] = perf_counter() - before
        report["rollback_returned"] = True
        mark(report, path, "close_writer")
        connection.close()
        connection = None

        mark(report, path, "new_connection_check")
        before = perf_counter()
        connection = connect(password)
        connection.start_transaction(
            isolation_level="REPEATABLE READ", consistent_snapshot=True, readonly=True)
        with connection.cursor() as cursor:
            report["counts_after_rollback"] = counts_for(cursor, run_id)
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Uptime'")
            report["uptime_after"] = int(cursor.fetchone()[1])
        connection.rollback()
        report["seconds"]["new_connection_check"] = perf_counter() - before
        if any(report["counts_after_rollback"].values()):
            raise ValueError("新连接仍发现诊断UUID的记录，停止后续操作")
        report["status"] = "verified_rolled_back"
        mark(report, path, "finished")
    except BaseException as exc:
        if insert_started is not None:
            report["seconds"]["insert_until_error"] = perf_counter() - insert_started
        report["status"] = "failed"
        report["error_at"] = now()
        report["error"] = {
            "type": type(exc).__name__, "errno": getattr(exc, "errno", None),
            "message": str(exc)[:500],
        }
        write_json(path, report)
        if connection is not None:
            try:
                connection.rollback()
                report["rollback_on_error"] = "returned"
            except Exception:
                report["rollback_on_error"] = "failed"
            write_json(path, report)
        raise SystemExit(
            f"写入诊断未通过，阶段：{report['phase']}；详情见 {path}"
        ) from None
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
    print("主INSERT执行成功，随后已回滚")
    print("新连接确认：诊断UUID在四张结果表中均为0条")
    print(f"主INSERT耗时：{report['seconds']['insert_run']:.6f} 秒")
    print(f"回滚耗时：{report['seconds']['rollback']:.6f} 秒")
    print("本次没有提交录取结果")
    print("报告：", path)


if __name__ == "__main__":
    main()