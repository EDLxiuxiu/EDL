from admissions_app.core.first_batch import ranks as first_batch_ranks
from admissions_app.core.first_batch import run_first_batch


CITY_CUTOFF = 600
GRADIENT_LINES = (720, 680, 640, 600)
CHOICE_LIMIT = 3

# 考生号、初中、总分、同分序号、第一批志愿、第二批志愿。
SAMPLE_ROWS = [
    ("S01", "J1", 700, 1, "A", "A"),
    ("S02", "J2", 714, 1, "A", "A"),
    ("S03", "J1", 760, 1, "A", "B"),
    ("S04", "J2", 699, 1, "A", ""),
    ("S05", "J1", 679, 1, "B", "B C"),
    ("S06", "J1", 730, 1, "", "A"),
    ("S07", "J1", 720, 1, "", "A"),
    ("S08", "J2", 676, 1, "", "B"),
    ("S16", "J2", 675, 1, "", "C"),
]

SAMPLE_PLANS = {
    "1-A": {"seats": 1, "cutoff": 700},
    "1-B": {"seats": 1, "cutoff": 680},
    "2-A-J1": {"seats": 1, "cutoff": 600},
    "2-A-J2": {"seats": 2, "cutoff": 600},
    "2-B-J1": {"seats": 1, "cutoff": 600},
    "2-B-J2": {"seats": 2, "cutoff": 600},
    "2-C-J1": {"seats": 1, "cutoff": 600},
    "2-C-J2": {"seats": 1, "cutoff": 680},
    "3-A": {"seats": 1},
    "3-B": {"seats": 1},
    "3-C": {"seats": 2},
}


def run_second_batch(candidates, plans, first_admitted, first_balances, *,
                     city_cutoff=CITY_CUTOFF, gradient_lines=GRADIENT_LINES,
                     choice_limit=CHOICE_LIMIT, ordered=None):
    balances = first_balances.copy()
    admitted = {plan_id: [] for plan_id in plans if plan_id.startswith("2-")}
    admitted_ids = {
        candidate_id
        for candidate_ids in first_admitted.values()
        for candidate_id in candidate_ids
    }
    transfers = []
    log = []

    # 从高分到低分，同分时按序号从小到大。
    if ordered is None:
        ordered = sorted(candidates, key=lambda c: (-c["score"], c["tie"]))
    groups = [[] for _ in gradient_lines]

    for candidate in ordered:
        candidate_id = candidate["id"]
        if candidate_id in admitted_ids:
            log.append(f"{candidate_id} 已在第一批录取，跳过第二批")
            continue
        if candidate["score"] < city_cutoff:
            log.append(f"{candidate_id} 低于全市控制线，跳过第二批")
            continue
        for index, line in enumerate(gradient_lines):
            if candidate["score"] >= line:
                groups[index].append(candidate)
                break

    for gradient, group in enumerate(groups, start=1):
        for choice_index in range(choice_limit):
            for candidate in group:
                candidate_id = candidate["id"]
                choices = candidate["second_choices"]
                if candidate_id in admitted_ids or choice_index >= len(choices):
                    continue

                school = choices[choice_index]
                plan_id = f"2-{school}-{candidate['junior']}"
                prefix = f"G{gradient} 志愿{choice_index + 1} {candidate_id} {plan_id}"

                if plan_id not in admitted:
                    log.append(f"{prefix} 无对应子计划")
                elif candidate["score"] < plans[plan_id]["cutoff"]:
                    log.append(f"{prefix} 低于计划控制线")
                elif balances[plan_id] == 0:
                    log.append(f"{prefix} 名额已满")
                else:
                    admitted[plan_id].append(candidate_id)
                    admitted_ids.add(candidate_id)
                    balances[plan_id] -= 1
                    log.append(f"{prefix} 已录取")

    # 先记录每个子计划转出的数量，再按高中汇总转入。
    incoming = {}
    for source in admitted:
        remaining = balances[source]
        if remaining == 0:
            continue
        school = source.split("-")[1]
        target = f"3-{school}"
        transfers.append((source, target, remaining))
        balances[source] = 0
        incoming[target] = incoming.get(target, 0) + remaining

    for target, seats in incoming.items():
        balances[target] += seats

    return admitted, balances, transfers, log


def main():
    candidates = [
        {
            "id": candidate_id,
            "junior": junior,
            "score": score,
            "tie": tie,
            "choice": first_choice,
            "second_choices": second_choices.split(),
        }
        for candidate_id, junior, score, tie, first_choice, second_choices in SAMPLE_ROWS
    ]
    first_admitted, _, first_balances, first_transfers = run_first_batch(
        candidates, first_batch_ranks, SAMPLE_PLANS, city_cutoff=CITY_CUTOFF
    )
    second_admitted, balances, second_transfers, log = run_second_batch(
        candidates, SAMPLE_PLANS, first_admitted, first_balances
    )

    print("两批录取人数")
    print("第一批", sum(len(ids) for ids in first_admitted.values()))
    print("第二批", sum(len(ids) for ids in second_admitted.values()))

    print("\n第二批投档日志")
    for message in log:
        print(message)

    print("\n第二批各计划录取名单")
    for plan_id, ids in second_admitted.items():
        print(plan_id, "、".join(ids) or "无")

    print("\n前两批结转流水")
    for source, target, seats in first_transfers + second_transfers:
        print(f"{source} -> {target} {seats} 个名额")

    print("\n第三批可用名额")
    for plan_id in ("3-A", "3-B", "3-C"):
        print(plan_id, balances[plan_id])


if __name__ == "__main__":
    main()
