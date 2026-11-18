import json
from getpass import getpass
from uuid import uuid4

import mysql.connector

from run_admissions import (
    CHOICE_LIMITS,
    check_balances,
    read_admission_inputs,
    run_all_batches,
)
from second_batch import CITY_CUTOFF, GRADIENT_LINES


def write_run(cursor, run_id, candidates, plans, ranks, result):
    input_snapshot = {
        "candidates": candidates,
        "plans": plans,
        "autonomous_ranks": ranks,
        "city_cutoff": CITY_CUTOFF,
        "gradient_lines": GRADIENT_LINES,
        "choice_limits": CHOICE_LIMITS,
    }
    decision_log = {
        key: result[key]
        for key in ("first_reasons", "second_log", "third_log", "fourth_log")
    }

    details = [
        (run_id, candidate_no, plan_id, order)
        for plan_id, candidate_ids in result["admitted"].items()
        for order, candidate_no in enumerate(candidate_ids, start=1)
    ]
    balances = [
        (run_id, plan_id, seats)
        for plan_id, seats in result["balances"].items()
    ]
    transfers = [
        (run_id, transfer_no, source, target, seats)
        for transfer_no, (source, target, seats)
        in enumerate(result["transfers"], start=1)
    ]

    cursor.execute(
        "INSERT INTO admission_runs "
        "(run_id, candidate_count, admitted_count, input_snapshot, decision_log) "
        "VALUES (%s, %s, %s, %s, %s)",
        (
            run_id,
            len(candidates),
            len(details),
            json.dumps(input_snapshot, ensure_ascii=False),
            json.dumps(decision_log, ensure_ascii=False),
        ),
    )
    cursor.executemany(
        "INSERT INTO run_plan_balances (run_id, plan_id, remaining_seats) "
        "VALUES (%s, %s, %s)",
        balances,
    )
    cursor.executemany(
        "INSERT INTO run_admission_details "
        "(run_id, candidate_no, plan_id, admission_order) VALUES (%s, %s, %s, %s)",
        details,
    )
    cursor.executemany(
        "INSERT INTO run_seat_transfers "
        "(run_id, transfer_no, source_plan_id, target_plan_id, seats) "
        "VALUES (%s, %s, %s, %s, %s)",
        transfers,
    )
    return len(details), len(balances), len(transfers)


def main():
    password = getpass("请输入 MySQL 应用账号 admission_app 的密码：")
    run_id = str(uuid4())
    print(f"运行编号 {run_id}", flush=True)

    try:
        with mysql.connector.connect(
            host="127.0.0.1",
            port=3306,
            user="admission_app",
            password=password,
            database="admissionsim",
            charset="utf8mb4",
            connection_timeout=5,
        ) as connection:
            connection.start_transaction(
                isolation_level="REPEATABLE READ",
                consistent_snapshot=True,
            )
            with connection.cursor() as cursor:
                candidates, plans, ranks = read_admission_inputs(cursor)
                result = run_all_batches(candidates, plans, ranks=ranks)
                check_balances(plans, result)
                admitted, balances, transfers = write_run(
                    cursor, run_id, candidates, plans, ranks, result
                )
            connection.commit()
    except mysql.connector.Error as exc:
        raise SystemExit(f"MySQL 保存失败（{exc.errno}）：{exc.msg}") from None

    print(f"保存成功，读入 {len(candidates)} 名考生")
    print(f"录取明细 {admitted} 条，计划余额 {balances} 条，结转流水 {transfers} 条")


if __name__ == "__main__":
    main()