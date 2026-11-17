from admission import ranks as first_batch_ranks
from admission import run_first_batch
from second_batch import CITY_CUTOFF, run_second_batch
from third_batch import PLANS, build_candidates, run_general_batch


CHOICE_LIMITS = {1: 1, 2: 3, 3: 6, 4: 6}


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


def run_all_batches(candidates, plans):
    validate_inputs(candidates, plans)

    first, first_reasons, balances, first_transfers = run_first_batch(
        candidates, first_batch_ranks, plans, city_cutoff=CITY_CUTOFF
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
    candidates = build_candidates()
    result = run_all_batches(candidates, PLANS)
    admitted = result["admitted"]
    balances = result["balances"]

    admitted_ids = set()
    for ids in admitted.values():
        for candidate_id in ids:
            if candidate_id in admitted_ids:
                raise ValueError(f"{candidate_id} 被重复录取")
            admitted_ids.add(candidate_id)

    check_balances(PLANS, result)

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
    print(f"{len(PLANS)} 条计划名额收支全部通过")


if __name__ == "__main__":
    main()