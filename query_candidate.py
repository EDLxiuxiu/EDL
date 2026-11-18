import argparse
import json
from getpass import getpass

import mysql.connector


def read_candidate(cursor, run_id, candidate_no):
    cursor.execute(
        "SELECT input_snapshot, decision_log FROM admission_runs WHERE run_id = %s",
        (run_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError(f"找不到运行编号 {run_id}")

    snapshot = json.loads(row[0])
    decision_log = json.loads(row[1])
    candidate = next(
        (item for item in snapshot["candidates"] if item["id"] == candidate_no),
        None,
    )
    if candidate is None:
        raise ValueError(f"该次运行的输入快照中没有考生 {candidate_no}")

    cursor.execute(
        "SELECT plan_id, admission_order FROM run_admission_details "
        "WHERE run_id = %s AND candidate_no = %s",
        (run_id, candidate_no),
    )
    admission = cursor.fetchone()
    return snapshot, candidate, decision_log, admission


def build_trace(snapshot, candidate, decision_log, admission):
    candidate_no = candidate["id"]
    saved_choices = candidate["choices"]
    choices_by_batch = {
        1: [candidate["choice"]] if candidate["choice"] else [],
        2: candidate["second_choices"],
        3: saved_choices.get("3", saved_choices.get(3, [])),
        4: saved_choices.get("4", saved_choices.get(4, [])),
    }
    admitted_batch = int(admission[0].split("-", 1)[0]) if admission else None
    trace = []

    for batch_no, choices in choices_by_batch.items():
        if batch_no == 1:
            reason = decision_log["first_reasons"].get(candidate_no)
            messages = [reason] if reason else []
        else:
            messages = [
                line for line in decision_log[
                    {2: "second_log", 3: "third_log", 4: "fourth_log"}[batch_no]
                ]
                if candidate_no in line.split()
            ]

        if not messages:
            if admitted_batch is not None and admitted_batch < batch_no:
                messages = ["已在前批录取，跳过本批"]
            elif not choices:
                messages = ["本批未填志愿"]
            elif candidate["score"] < snapshot["city_cutoff"]:
                messages = ["低于全市控制线"]
            else:
                messages = ["该批日志未记录处理原因"]

        trace.append((batch_no, choices, messages))

    return trace


def print_candidate(run_id, candidate, admission, trace):
    print(f"\n运行编号 {run_id}")
    print(f"考生 {candidate['id']}，生源初中 {candidate['junior']}")
    print(f"总分 {candidate['score']}，同分序号 {candidate['tie']}")

    if admission:
        plan_id, admission_order = admission
        print(f"最终去向 {plan_id}，该计划实际录取顺序第 {admission_order} 位")
    else:
        print("最终去向 未录取")

    for batch_no, choices, messages in trace:
        print(f"\n第 {batch_no} 批志愿 {' -> '.join(choices) or '无'}")
        for message in messages:
            print(message)


def main():
    parser = argparse.ArgumentParser(description="按运行编号查询考生处理原因")
    parser.add_argument("run_id", help="已保存的运行编号")
    parser.add_argument("candidate_no", help="考生号，例如 S19")
    args = parser.parse_args()
    candidate_no = args.candidate_no.strip().upper()
    password = getpass("请输入 MySQL 应用账号 admission_app 的密码：")

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
                readonly=True,
            )
            with connection.cursor() as cursor:
                snapshot, candidate, decision_log, admission = read_candidate(
                    cursor, args.run_id, candidate_no
                )
    except (mysql.connector.Error, ValueError) as exc:
        raise SystemExit(f"查询考生失败：{exc}") from None

    trace = build_trace(snapshot, candidate, decision_log, admission)
    print_candidate(args.run_id, candidate, admission, trace)


if __name__ == "__main__":
    main()