import argparse
import hashlib
from datetime import datetime, timedelta, timezone
from getpass import getpass
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from tools.legacy.save_scale_run import (
    DATABASE, LOG_KEYS, command_bytes, connect, load_local,
    read_json, serialize, sha, write_json,
)

SQL = (
    "SELECT JSON_TYPE(s), JSON_LENGTH(s, '$.candidates'), "
    "JSON_LENGTH(s, '$.plans'), JSON_TYPE(l), "
    "JSON_LENGTH(l, '$.first_reasons'), JSON_LENGTH(l, '$.second_log'), "
    "JSON_LENGTH(l, '$.third_log'), JSON_LENGTH(l, '$.fourth_log'), "
    "JSON_STORAGE_SIZE(s), JSON_STORAGE_SIZE(l) "
    "FROM (SELECT CAST(%s AS JSON) AS s, CAST(%s AS JSON) AS l) AS payload_probe"
)


def main():
    parser = argparse.ArgumentParser(description="只读检查完整快照和日志的MySQL JSON转换")
    parser.add_argument(
        "--save-report", type=Path,
        default=Path("experiments/mysql_save_150000_v1/save_report.json"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    report_path = args.out / "json_probe_report.json"
    report = {
        "status": "preparing", "phase": "local_prepare",
        "diagnostic_id": str(uuid4()), "database": DATABASE,
        "started_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "save_report": str(args.save_report.resolve()),
        "source_sha256": {
            "probe": sha(__file__), "tools/legacy/save_scale_run.py": sha("tools/legacy/save_scale_run.py")},
        "conditions": {
            "readonly": True, "measurement_runs": 1,
            "scope": "JSON转换、属性计算和二进制表示大小；没有结果表写入",
            "timing": "SELECT耗时含驱动编码、传输及服务器计算，不是纯JSON转换时间",
        },
        "seconds": {},
    }
    write_json(report_path, report)
    connection = None
    query_started = None
    try:
        before = perf_counter()
        saved = read_json(args.save_report)
        if saved["database"] != DATABASE:
            raise ValueError("原报告目标库不符")
        loaded = load_local(saved["paths"])
        data, _, result = loaded[:3]
        expected = [
            "OBJECT", len(data["candidates"]), len(data["plans"]), "OBJECT",
            *[len(result[key]) for key in LOG_KEYS],
        ]
        payload = serialize(loaded[4], result)
        del loaded, data, result
        digests = [hashlib.sha256(v.encode("utf-8")).hexdigest() for v in payload]
        if digests != saved["payload_sha256"]:
            raise ValueError("两份JSON文本与原保存报告不一致")
        report.update(source_run_id=saved["run_id"], expected_prefix=expected,
                      payload_sha256=digests, sql=SQL)
        report["seconds"]["local_prepare"] = perf_counter() - before
        report["phase"] = "connect"
        write_json(report_path, report)
        print("目标库：", DATABASE, flush=True)
        password = getpass("请输入 admission_app 的应用密码：")
        before = perf_counter()
        connection = connect(password)
        report["seconds"]["connect"] = perf_counter() - before
        connection.start_transaction(
            isolation_level="REPEATABLE READ", consistent_snapshot=True, readonly=True)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT DATABASE(), VERSION(), @@SESSION.max_allowed_packet, "
                "@@SESSION.sql_mode, @@SESSION.net_read_timeout, "
                "@@SESSION.net_write_timeout, @@GLOBAL.log_bin")
            database, version, packet, mode, rt, wt, log_bin = cursor.fetchone()
            if database != DATABASE:
                raise ValueError("实际连接库不符")
            report["server"] = {
                "version": version, "max_allowed_packet": packet, "sql_mode": mode,
                "net_read_timeout": rt, "net_write_timeout": wt, "log_bin": log_bin,
            }
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Uptime'")
            report["uptime_before"] = int(cursor.fetchone()[1])
            size = command_bytes(SQL, payload, connection.converter, mode)
            report["command_bytes"] = size
            if size >= packet - 1024**2:
                raise ValueError("语句距会话发送上限不足1 MiB")
            report.update(status="running", phase="json_select")
            write_json(report_path, report)
            print(f"JSON诊断语句：{size / 1024**2:.2f} MiB", flush=True)
            print("正在转换JSON并检查内容计数及二进制表示大小……", flush=True)
            query_started = perf_counter()
            cursor.execute(SQL, payload)
            actual = cursor.fetchone()
            report["seconds"]["json_select"] = perf_counter() - query_started
            query_started = None
            report["actual"] = list(actual) if actual is not None else None
            if actual is None or len(actual) != 10 or list(actual[:8]) != expected:
                raise ValueError("JSON类型或内容计数不一致")
            if any(type(n) is not int or n <= 0 for n in actual[8:]):
                raise ValueError("没有取得有效的JSON二进制表示大小")
            report["phase"] = "postcheck"
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Uptime'")
            report["uptime_after"] = int(cursor.fetchone()[1])
        connection.rollback()
        report.update(status="verified", phase="finished")
        write_json(report_path, report)
    except BaseException as exc:
        if query_started is not None:
            report["seconds"]["json_select_until_error"] = perf_counter() - query_started
        report["status"] = "failed"
        report["error"] = {
            "type": type(exc).__name__, "errno": getattr(exc, "errno", None),
            "message": str(exc)[:500],
        }
        write_json(report_path, report)
        raise SystemExit(
            f"JSON诊断未通过：{type(exc).__name__}；详情见 {report_path}"
        ) from None
    finally:
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass
            try:
                connection.close()
            except Exception:
                pass
    print("JSON转换和内容计数检查通过")
    print(f"考生 {actual[1]}，计划 {actual[2]}，日志 {sum(actual[4:8])}")
    print(f"快照二进制表示 {actual[8]} 字节，日志二进制表示 {actual[9]} 字节")
    print(f"本次诊断SELECT耗时：{report['seconds']['json_select']:.6f} 秒")
    print("本次没有保存任何录取结果")
    print("报告：", report_path)


if __name__ == "__main__":
    main()