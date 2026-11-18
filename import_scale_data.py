import argparse
import hashlib
import json
import platform
import sys
from datetime import datetime, timedelta, timezone
from getpass import getpass
from itertools import islice, zip_longest
from pathlib import Path
from time import perf_counter

import mysql.connector
from generate_scale_data import summarize, validate_dataset

DATABASE = "admissionsim_scale_150000"
TABLES = {
    "junior_schools": ("school_code", "school_name"),
    "senior_schools": ("school_code", "school_name"),
    "candidates": ("candidate_no", "candidate_name", "junior_code", "score", "tie_no"),
    "admission_plans": (
        "plan_id", "batch_no", "senior_code", "source_junior_code",
        "initial_seats", "cutoff_score"),
    "autonomous_qualifications": ("candidate_no", "senior_code", "assessment_rank"),
    "candidate_choices": ("candidate_no", "batch_no", "choice_no", "senior_code"),
}
ORDERS = {
    "junior_schools": "school_code", "senior_schools": "school_code",
    "candidates": "candidate_no", "admission_plans": "plan_id",
    "autonomous_qualifications": "candidate_no, senior_code",
    "candidate_choices": "candidate_no, batch_no, choice_no",
}
ALL_TABLES = (*TABLES, "experiment_dataset", "admission_runs",
              "run_plan_balances", "run_admission_details", "run_seat_transfers")


def read_source(directory):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    raw = (directory / "data.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest["data_sha256"]:
        raise ValueError("数据文件 SHA256 与清单不一致")
    data = json.loads(raw)
    del raw
    for c in data["candidates"]:
        c["choices"] = {int(k): v for k, v in c["choices"].items()}
    validate_dataset(data)
    if data["config"] != manifest["config"] or summarize(data) != manifest["counts"]:
        raise ValueError("数据内容与清单不一致")
    if len(data["candidates"]) != 150000:
        raise ValueError("本脚本仅用于该独立实验库的 15 万人数据")
    return data, manifest


def rows_for(table, data):
    if table == "junior_schools":
        for code, name in sorted(data["junior_schools"].items()):
            yield code, name
    elif table == "senior_schools":
        for code, info in sorted(data["senior_schools"].items()):
            yield code, info["name"]
    elif table == "admission_plans":
        for plan_id, plan in sorted(data["plans"].items()):
            parts = plan_id.split("-")
            yield (plan_id, int(parts[0]), parts[1],
                   parts[2] if len(parts) == 3 else None,
                   plan["seats"], plan["cutoff"])
    elif table == "autonomous_qualifications":
        yield from sorted(
            (candidate_id, school, rank)
            for school, ranks in data["autonomous_ranks"].items()
            for candidate_id, rank in ranks.items())
    else:
        for c in sorted(data["candidates"], key=lambda item: item["id"]):
            if table == "candidates":
                yield c["id"], c["name"], c["junior"], c["score"], c["tie"]
            elif table == "candidate_choices":
                choices = {
                    1: [c["choice"]] if c["choice"] else [],
                    2: c["second_choices"],
                    3: c["choices"][3], 4: c["choices"][4],
                }
                for batch, schools in choices.items():
                    for order, school in enumerate(schools, 1):
                        yield c["id"], batch, order, school


def expected_counts(manifest):
    c = manifest["counts"]
    return {
        "junior_schools": c["juniors"], "senior_schools": c["seniors"],
        "candidates": c["candidates"], "admission_plans": c["plans"],
        "autonomous_qualifications": c["qualified_rows"],
        "candidate_choices": c["choice_rows"], "experiment_dataset": 1,
    }


def table_counts(cursor):
    result = {}
    for table in ALL_TABLES:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        result[table] = cursor.fetchone()[0]
    return result


def require_empty(cursor):
    cursor.execute(
        "SELECT table_name, engine FROM information_schema.tables "
        "WHERE table_schema = %s", (DATABASE,))
    engines = dict(cursor)
    if any(engines.get(table, "").upper() != "INNODB" for table in ALL_TABLES):
        raise ValueError("缺少所需表或存在非 InnoDB 表，请先核对 schema_scale.sql")
    counts = table_counts(cursor)
    if any(counts.values()):
        raise ValueError("实验库非空，拒绝重复导入；请先使用 --check 核对")


def insert_table(cursor, table, data, batch_size):
    columns = TABLES[table]
    sql = (f"INSERT INTO {table} ({', '.join(columns)}) VALUES "
           f"({', '.join(['%s'] * len(columns))})")
    source = iter(rows_for(table, data))
    count = 0
    while True:
        chunk = list(islice(source, batch_size))
        if not chunk:
            break
        cursor.executemany(sql, chunk)
        previous = count
        count += len(chunk)
        if count // 100000 != previous // 100000:
            print(f"  {table} 已发送 {count} 行（尚未提交）", flush=True)
    return count


def verify_database(cursor, data, manifest, full=True):
    cursor.execute(
        "SELECT dataset_id, data_sha256, manifest "
        "FROM experiment_dataset WHERE dataset_slot = 1")
    saved = cursor.fetchone()
    if saved is None or saved[:2] != (manifest["dataset_id"], manifest["data_sha256"]):
        raise ValueError("数据库中没有匹配的数据集清单")
    stored = json.loads(saved[2])
    if stored["config"] != manifest["config"] or stored["counts"] != manifest["counts"]:
        raise ValueError("数据库清单中的配置或计数不一致")
    counts = table_counts(cursor)
    for table, expected in expected_counts(manifest).items():
        if counts[table] != expected:
            raise ValueError(f"{table} 行数不符：{counts[table]} != {expected}")
    cursor.execute("SELECT COALESCE(SUM(initial_seats), 0) FROM admission_plans")
    if cursor.fetchone()[0] != manifest["counts"]["initial_seats"]:
        raise ValueError("初始名额合计不一致")
    if full:
        for table, columns in TABLES.items():
            cursor.execute(
                f"SELECT {', '.join(columns)} FROM {table} ORDER BY {ORDERS[table]}")
            mismatch = None
            # 即使发现差异也读完结果，避免连接留下未消费的结果集。
            for index, (actual, expected) in enumerate(
                    zip_longest(cursor, rows_for(table, data)), 1):
                if actual != expected and mismatch is None:
                    mismatch = index
            if mismatch is not None:
                raise ValueError(f"{table} 第 {mismatch} 行与源数据不一致")
            print(f"  内容核对通过：{table}", flush=True)
    return counts


def connect(password):
    return mysql.connector.connect(
        host="127.0.0.1", port=3306, user="admission_app",
        password=password, database=DATABASE, charset="utf8mb4",
        autocommit=False, connection_timeout=10)


def check_committed(password, data, manifest, full):
    with connect(password) as connection:
        connection.start_transaction(
            isolation_level="REPEATABLE READ", consistent_snapshot=True, readonly=True)
        with connection.cursor() as cursor:
            return verify_database(cursor, data, manifest, full=full)


def write_report(path, report):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description="向独立实验库分批导入 15 万人数据")
    parser.add_argument("--dataset", type=Path, default=Path("experiments/scale_150000"))
    parser.add_argument("--out", type=Path, help="新的测量报告目录")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--check", action="store_true", help="只读核对已导入的数据")
    args = parser.parse_args()
    if not 100 <= args.batch_size <= 5000:
        parser.error("--batch-size 应在 100 至 5000 之间")
    if not args.check and args.out is None:
        parser.error("导入时必须指定 --out")
    before = perf_counter()
    data, manifest = read_source(args.dataset.resolve())
    seconds = {"source_load_validate": perf_counter() - before}
    print("数据集：", manifest["dataset_id"], flush=True)
    print("目标库：", DATABASE, flush=True)
    password = getpass("请输入 admission_app 的应用密码：")
    if args.check:
        counts = check_committed(password, data, manifest, full=True)
        print("已提交数据的行数、名额、配置和逐行内容全部核对通过")
        print(json.dumps(counts, ensure_ascii=False, indent=2))
        return

    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    report_path = args.out / "import_report.json"
    report = {
        "status": "prepared", "database": DATABASE,
        "dataset_id": manifest["dataset_id"], "data_sha256": manifest["data_sha256"],
        "batch_size": args.batch_size, "expected_counts": expected_counts(manifest),
        "started_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "environment": {"python": sys.version, "platform": platform.platform(),
                        "connector": mysql.connector.__version__},
        "source_sha256": {
            name: hashlib.sha256(Path(name).read_bytes()).hexdigest()
            for name in (__file__, "generate_scale_data.py", "schema_scale.sql")},
        "seconds": seconds,
        "conditions": "单事务；按批发送；写入计时含行生成，不含源文件读取和内容核对",
    }
    write_report(report_path, report)
    connection = None
    commit_attempted = committed = False
    try:
        before = perf_counter()
        connection = connect(password)
        seconds["connect"] = perf_counter() - before
        connection.start_transaction(isolation_level="REPEATABLE READ")
        with connection.cursor() as cursor:
            before = perf_counter()
            require_empty(cursor)
            cursor.execute("SELECT VERSION(), @@GLOBAL.max_allowed_packet, @@GLOBAL.log_bin")
            version, packet, log_bin = cursor.fetchone()
            report["server"] = {
                "version": version, "max_allowed_packet": packet, "log_bin": log_bin}
            seconds["precheck"] = perf_counter() - before

            before = perf_counter()
            # 唯一的数据集槽位也用于阻止两个导入程序重复占用此库。
            cursor.execute(
                "INSERT INTO experiment_dataset "
                "(dataset_slot, dataset_id, data_sha256, manifest) VALUES (1, %s, %s, %s)",
                (manifest["dataset_id"], manifest["data_sha256"],
                 json.dumps(manifest, ensure_ascii=False)))
            seconds["manifest_insert"] = perf_counter() - before
            for table in TABLES:
                print("正在导入：", table, flush=True)
                before = perf_counter()
                count = insert_table(cursor, table, data, args.batch_size)
                seconds[f"insert_{table}"] = perf_counter() - before
                if count != expected_counts(manifest)[table]:
                    raise ValueError(f"{table} 生成的待导入行数不一致")
                print(f"  已发送 {count} 行", flush=True)

            before = perf_counter()
            verify_database(cursor, data, manifest, full=True)
            seconds["verify_before_commit"] = perf_counter() - before

        report["status"] = "commit_pending"
        write_report(report_path, report)
        print("内容核对通过，正在提交事务……", flush=True)
        commit_attempted = True
        before = perf_counter()
        connection.commit()
        seconds["commit"] = perf_counter() - before
        committed = True
        report["status"] = "committed"
        write_report(report_path, report)
        connection.close()
        connection = None

        before = perf_counter()
        report["actual_counts"] = check_committed(password, data, manifest, full=False)
        seconds["post_commit_check"] = perf_counter() - before
        seconds["write_and_commit"] = seconds["manifest_insert"] + seconds["commit"] + sum(
            seconds[f"insert_{table}"] for table in TABLES)
        report["status"] = "verified"
        write_report(report_path, report)
    except BaseException as exc:
        if connection is not None and not committed:
            try:
                connection.rollback()
            except Exception:
                pass
        report["status"] = (
            "committed_check_failed" if committed else
            "commit_unknown" if commit_attempted else "not_committed")
        report["error"] = f"{type(exc).__name__}: {exc}"
        try:
            write_report(report_path, report)
        except Exception:
            pass
        print("本次状态：", report["status"], flush=True)
        if commit_attempted:
            print("请先运行 --check 核对数据库提交结果，不自动重试。", flush=True)
        raise
    finally:
        if connection is not None:
            connection.close()

    print("\n导入成功，提交后检查通过")
    print(json.dumps(report["actual_counts"], ensure_ascii=False, indent=2))
    for stage, value in seconds.items():
        print(f"{stage}: {value:.6f} 秒")
    print("报告：", report_path)


if __name__ == "__main__":
    main()