import argparse
import hashlib
from datetime import datetime, timedelta, timezone
from getpass import getpass
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from tools.legacy.save_scale_run import (
    DATABASE, command_bytes, connect, load_local,
    read_json, serialize, sha, write_json,
)

PROBE_SQL = (
    "SELECT OCTET_LENGTH(snapshot_text), SHA2(snapshot_text, 256), "
    "OCTET_LENGTH(log_text), SHA2(log_text, 256) "
    "FROM (SELECT %s AS snapshot_text, %s AS log_text) AS payload_probe"
)


def main():
    parser = argparse.ArgumentParser(description="只读检查完整快照和日志的大语句传输")
    parser.add_argument(
        "--save-report", type=Path,
        default=Path("experiments/mysql_save_150000_v1/save_report.json"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    report_path = args.out / "probe_report.json"
    report = {
        "status": "preparing", "phase": "local_prepare",
        "diagnostic_id": str(uuid4()), "database": DATABASE,
        "started_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "save_report": str(args.save_report.resolve()),
        "source_sha256": sha(__file__),
        "conditions": {
            "readonly": True, "measurement_runs": 1,
            "driver": "use_pure=True；复用原连接函数",
            "scope": "传输两份原始JSON文本，返回长度和SHA256；不执行结果保存",
        },
        "seconds": {},
    }
    write_json(report_path, report)
    connection = None
    probe_started = None
    try:
        before = perf_counter()
        saved = read_json(args.save_report)
        if saved["database"] != DATABASE:
            raise ValueError("原保存报告的目标库不符")
        loaded = load_local(saved["paths"])
        payload = serialize(loaded[4], loaded[2])
        del loaded
        expected = []
        digests = []
        for value in payload:
            raw = value.encode("utf-8")
            digest = hashlib.sha256(raw).hexdigest()
            expected.extend((len(raw), digest))
            digests.append(digest)
            del raw
        if digests != saved["payload_sha256"]:
            raise ValueError("重建快照或日志与原保存报告不一致，请保持原文件不变")
        report["source_run_id"] = saved["run_id"]
        report["expected"] = expected
        report["seconds"]["local_prepare"] = perf_counter() - before
        report["phase"] = "connect"
        write_json(report_path, report)
        print("目标库：", DATABASE, flush=True)
        print("原运行编号：", saved["run_id"], flush=True)
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
                "@@SESSION.net_write_timeout, CONNECTION_ID()")
            (
                database, version, packet, mode,
                read_timeout, write_timeout, connection_id
            ) = cursor.fetchone()
            if database != DATABASE:
                raise ValueError("实际连接库不符")
            report["server"] = {
                "version": version, "max_allowed_packet": packet, "sql_mode": mode,
                "net_read_timeout": read_timeout, "net_write_timeout": write_timeout,
                "connection_id": connection_id,
            }
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Uptime'")
            report["uptime_before"] = int(cursor.fetchone()[1])
            size = command_bytes(PROBE_SQL, payload, connection.converter, mode)
            report["command_bytes"] = size
            if size >= packet - 1024**2:
                raise ValueError("诊断语句距会话发送上限不足1 MiB，停止发送")
            report["status"] = "running"
            report["phase"] = "large_select"
            write_json(report_path, report)
            print(f"只读SELECT命令：{size / 1024**2:.2f} MiB", flush=True)
            print("正在发送；只返回长度和摘要……", flush=True)
            probe_started = perf_counter()
            cursor.execute(PROBE_SQL, payload)
            actual = cursor.fetchone()
            report["seconds"]["large_select"] = perf_counter() - probe_started
            probe_started = None
            report["actual"] = list(actual) if actual is not None else None
            if actual is None or list(actual) != expected:
                raise ValueError("服务器返回的长度或SHA256与本地不一致")
            report["phase"] = "postcheck"
            cursor.execute("SHOW GLOBAL STATUS LIKE 'Uptime'")
            report["uptime_after"] = int(cursor.fetchone()[1])
        connection.rollback()
        report["phase"] = "finished"
        report["status"] = "verified"
        write_json(report_path, report)
    except BaseException as exc:
        if probe_started is not None:
            report["seconds"]["large_select_until_error"] = perf_counter() - probe_started
        report["status"] = "failed"
        report["error"] = {
            "type": type(exc).__name__, "errno": getattr(exc, "errno", None),
            "message": str(exc)[:500],
        }
        write_json(report_path, report)
        raise SystemExit(
            f"诊断未通过：{type(exc).__name__}；详情见 {report_path}"
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
    print("只读大语句检查通过：两份文本的长度和SHA256均一致")
    print(f"诊断SELECT耗时：{report['seconds']['large_select']:.6f} 秒")
    print("本次没有保存任何录取结果")
    print("报告：", report_path)


if __name__ == "__main__":
    main()