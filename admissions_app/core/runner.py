import argparse
from getpass import getpass

from admissions_app.core.first_batch import ranks as first_batch_ranks
from admissions_app.core.first_batch import run_first_batch
from admissions_app.core.second_batch import CITY_CUTOFF, run_second_batch
from admissions_app.core.third_batch import PLANS, build_candidates, run_general_batch


CHOICE_LIMITS = {1: 1, 2: 3, 3: 6, 4: 6}


def read_admission_inputs(cursor):
    cursor.execute(
        "SELECT candidate_no, junior_code, score, tie_no "
        "FROM candidates ORDER BY candidate_no"
    )
    by_id = {
        candidate_no: {
            "id": candidate_no,
            "junior": junior_code,
            "score": score,
            "tie": tie_no,
            "choice": "",
            "second_choices": [],
            "choices": {3: [], 4: []},
        }
        for candidate_no, junior_code, score, tie_no in cursor
    }

    cursor.execute(
        "SELECT candidate_no, batch_no, senior_code FROM candidate_choices "
        "ORDER BY candidate_no, batch_no, choice_no"
    )
    for candidate_no, batch_no, senior_code in cursor:
        candidate = by_id[candidate_no]
        if batch_no == 1:
            candidate["choice"] = senior_code
        elif batch_no == 2:
            candidate["second_choices"].append(senior_code)
        else:
            candidate["choices"][batch_no].append(senior_code)

    cursor.execute(
        "SELECT plan_id, initial_seats, cutoff_score "
        "FROM admission_plans ORDER BY plan_id"
    )
    plans = {
        plan_id: {"seats": seats, "cutoff": cutoff}
        for plan_id, seats, cutoff in cursor
    }

    cursor.execute(
        "SELECT senior_code, candidate_no, assessment_rank "
        "FROM autonomous_qualifications"
    )
    ranks = {}
    for senior_code, candidate_no, assessment_rank in cursor:
        ranks.setdefault(senior_code, {})[candidate_no] = assessment_rank

    return list(by_id.values()), plans, ranks


def load_mysql_inputs():
    import mysql.connector

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
                return read_admission_inputs(cursor)
    except mysql.connector.Error as exc:
        raise SystemExit(f"MySQL 读取失败（{exc.errno}）：{exc.msg}") from None


def validate_inputs(candidates, plans):
    schools_by_batch = {batch: set() for batch in CHOICE_LIMITS}
    for plan_id in plans:
        batch, school, *_ = plan_id.split("-")
        schools_by_batch[int(batch)].add(school)

    seen_ids = set()
    tie_owners = {}

    for candidate in candidates:
        candidate_id = candidate["id"]
        if candidate_id in seen_ids:
            raise ValueError(f"重复考生编号：{candidate_id}")
        seen_ids.add(candidate_id)

        score = candidate["score"]
        if type(score) is not int or score < 0:
            raise ValueError(f"{candidate_id} 的总分必须是非负整数，实际为 {score!r}")

        tie = candidate["tie"]
        if type(tie) is not int or tie < 1:
            raise ValueError(f"{candidate_id} 的同分序号必须是正整数，实际为 {tie!r}")

        tie_key = (score, tie)
        if tie_key in tie_owners:
            previous_id = tie_owners[tie_key]
            raise ValueError(
                f"{candidate_id} 与 {previous_id} 的同分序号重复：{score} 分、序号 {tie}"
            )
        tie_owners[tie_key] = candidate_id

        first_choice = candidate["choice"]
        choices_by_batch = {
            1: [first_choice] if first_choice else [],
            2: candidate["second_choices"],
            3: candidate["choices"][3],
            4: candidate["choices"][4],
        }

        for batch, choices in choices_by_batch.items():
            limit = CHOICE_LIMITS[batch]
            if len(choices) > limit:
                raise ValueError(f"{candidate_id} 第 {batch} 批最多可填 {limit} 个志愿")

            seen_schools = set()
            for school in choices:
                if school in seen_schools:
                    raise ValueError(f"{candidate_id} 第 {batch} 批重复填报学校 {school}")
                if school not in schools_by_batch[batch]:
                    raise ValueError(
                        f"{candidate_id} 第 {batch} 批填报的学校 {school} 无招生计划"
                    )
                seen_schools.add(school)


def run_all_batches(candidates, plans, ranks=None):
    validate_inputs(candidates, plans)
    if ranks is None:
        ranks = first_batch_ranks

    first, first_reasons, balances, first_transfers = run_first_batch(
        candidates, ranks, plans, city_cutoff=CITY_CUTOFF
    )
    second, balances, second_transfers, second_log = run_second_batch(
        candidates, plans, first, balances
    )
    previous_admitted = {**first, **second}

    third, balances, third_log = run_general_batch(
        3, candidates, plans, previous_admitted, balances
    )
    previous_admitted.update(third)

    fourth, balances, fourth_log = run_general_batch(
        4, candidates, plans, previous_admitted, balances
    )
    admitted = {**previous_admitted, **fourth}

    return {
        "admitted": admitted,
        "balances": balances,
        "transfers": first_transfers + second_transfers,
        "first_reasons": first_reasons,
        "second_log": second_log,
        "third_log": third_log,
        "fourth_log": fourth_log,
    }


def check_balances(plans, result):
    transferred_in = dict.fromkeys(plans, 0)
    transferred_out = dict.fromkeys(plans, 0)

    for source, target, seats in result["transfers"]:
        transferred_out[source] += seats
        transferred_in[target] += seats

    for plan_id, plan in plans.items():
        available = plan["seats"] + transferred_in[plan_id] - transferred_out[plan_id]
        recorded = len(result["admitted"][plan_id]) + result["balances"][plan_id]
        if available != recorded:
            raise ValueError(f"{plan_id} 名额收支不平：{available} != {recorded}")


def main():
    parser = argparse.ArgumentParser(description="运行四批高中录取模拟")
    parser.add_argument("--mysql", action="store_true", help="从 MySQL 读取投档数据")
    args = parser.parse_args()

    if args.mysql:
        candidates, plans, ranks = load_mysql_inputs()
        print(f"数据来源 MySQL admissionsim，读入 {len(candidates)} 名考生、{len(plans)} 条计划")
    else:
        candidates, plans, ranks = build_candidates(), PLANS, first_batch_ranks

    result = run_all_batches(candidates, plans, ranks=ranks)
    admitted = result["admitted"]
    balances = result["balances"]

    admitted_ids = set()
    for ids in admitted.values():
        for candidate_id in ids:
            if candidate_id in admitted_ids:
                raise ValueError(f"{candidate_id} 被重复录取")
            admitted_ids.add(candidate_id)

    check_balances(plans, result)

    print("各批录取人数")
    for batch in range(1, 5):
        count = sum(
            len(ids) for plan_id, ids in admitted.items()
            if plan_id.startswith(f"{batch}-")
        )
        print(f"第 {batch} 批 {count} 人")

    print("\n第四批投档日志")
    for message in result["fourth_log"]:
        print(message)

    print("\n各计划录取顺序与余额")
    for plan_id, ids in admitted.items():
        names = " -> ".join(ids) or "无"
        print(f"{plan_id} {names}，余额 {balances[plan_id]}")

    print("\n结转流水")
    for source, target, seats in result["transfers"]:
        print(f"{source} -> {target} {seats} 个名额")

    unadmitted = sorted(
        candidate["id"] for candidate in candidates
        if candidate["id"] not in admitted_ids
    )
    print(f"\n总录取 {len(admitted_ids)} 人")
    print("未录取", "、".join(unadmitted) or "无")
    print(f"剩余名额 {sum(balances.values())}")
    print(f"{len(plans)} 条计划名额收支全部通过")


if __name__ == "__main__":
    main()