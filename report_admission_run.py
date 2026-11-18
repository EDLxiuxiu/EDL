import argparse
import json
from getpass import getpass

import mysql.connector


def read_run(cursor, run_id):
    cursor.execute(
        "SELECT input_snapshot FROM admission_runs WHERE run_id = %s",
        (run_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError(f"找不到运行编号 {run_id}")
    snapshot = json.loads(row[0])

    admitted = {plan_id: [] for plan_id in snapshot["plans"]}
    cursor.execute(
        "SELECT plan_id, candidate_no FROM run_admission_details "
        "WHERE run_id = %s ORDER BY plan_id, admission_order",
        (run_id,),
    )
    for plan_id, candidate_no in cursor:
        admitted[plan_id].append(candidate_no)

    cursor.execute(
        "SELECT plan_id, remaining_seats FROM run_plan_balances WHERE run_id = %s",
        (run_id,),
    )
    balances = dict(cursor)
    return snapshot, admitted, balances


def build_report(snapshot, admitted, balances):
    scores = {
        candidate["id"]: candidate["score"]
        for candidate in snapshot["candidates"]
    }
    admitted_ids = {
        candidate_no
        for candidate_ids in admitted.values()
        for candidate_no in candidate_ids
    }
    batch_counts = dict.fromkeys(range(1, 5), 0)
    plan_rows = []

    for plan_id, plan in sorted(snapshot["plans"].items()):
        candidate_ids = admitted[plan_id]
        batch_no = int(plan_id.split("-", 1)[0])
        batch_counts[batch_no] += len(candidate_ids)
        last_id = candidate_ids[-1] if candidate_ids else None

        plan_rows.append({
            "plan_id": plan_id,
            "initial_seats": plan["seats"],
            "admitted_count": len(candidate_ids),
            "remaining_seats": balances[plan_id],
            "lowest_score": min((scores[c] for c in candidate_ids), default=None),
            "last_id": last_id,
            "last_score": scores[last_id] if last_id is not None else None,
        })

    return {
        "candidate_count": len(scores),
        "admitted_count": len(admitted_ids),
        "initial_seats": sum(row["initial_seats"] for row in plan_rows),
        "remaining_seats": sum(row["remaining_seats"] for row in plan_rows),
        "unadmitted": sorted(scores.keys() - admitted_ids),
        "batch_counts": batch_counts,
        "plan_rows": plan_rows,
    }


def percentage(numerator, denominator):
    if denominator == 0:
        return "无"
    return f"{numerator / denominator:.2%}"


def print_report(run_id, report):
    admitted_count = report["admitted_count"]
    print(f"\n运行编号 {run_id}")
    print(f"考生 {report['candidate_count']} 人，录取 {admitted_count} 人")
    print(f"初始名额 {report['initial_seats']}，剩余名额 {report['remaining_seats']}")
    print("考生录取率", percentage(admitted_count, report["candidate_count"]))
    print("名额完成率", percentage(admitted_count, report["initial_seats"]))
    print("未录取", "、".join(report["unadmitted"]) or "无")

    print("\n各批录取人数")
    for batch_no, count in report["batch_counts"].items():
        print(f"第 {batch_no} 批 {count} 人")

    print("\n逐计划统计")
    for row in report["plan_rows"]:
        lowest = row["lowest_score"]
        lowest_text = "无" if lowest is None else f"{lowest} 分"
        last_text = "无"
        if row["last_id"] is not None:
            last_text = f"{row['last_id']}（{row['last_score']} 分）"

        print(
            f"{row['plan_id']} | 初始 {row['initial_seats']} | "
            f"录取 {row['admitted_count']} | 余额 {row['remaining_seats']} | "
            f"最低分 {lowest_text} | 末位 {last_text}"
        )


def main():
    parser = argparse.ArgumentParser(description="查询已保存的投档运行统计")
    parser.add_argument("run_id", help="保存投档时输出的运行编号")
    args = parser.parse_args()
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
                snapshot, admitted, balances = read_run(cursor, args.run_id)
    except (mysql.connector.Error, ValueError) as exc:
        raise SystemExit(f"读取运行记录失败：{exc}") from None

    report = build_report(snapshot, admitted, balances)
    print_report(args.run_id, report)


if __name__ == "__main__":
    main()