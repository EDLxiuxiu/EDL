from admissions_app.core.first_batch import ranks as first_batch_ranks
from admissions_app.core.first_batch import run_first_batch
from admissions_app.core.second_batch import CITY_CUTOFF, GRADIENT_LINES, run_second_batch
from admissions_app.core.second_batch import SAMPLE_PLANS as EARLY_PLANS


# 考生号、初中、总分、同分序号、第一批至第四批志愿。
SAMPLE_ROWS = [
    ("S01", "J1", 700, 1, "A", "A", "A", "D"),
    ("S02", "J2", 714, 1, "A", "A", "A", "D"),
    ("S03", "J1", 760, 1, "A", "B", "B", "D"),
    ("S04", "J2", 699, 1, "A", "", "B", ""),
    ("S05", "J1", 679, 1, "B", "B C", "C", ""),
    ("S06", "J1", 730, 1, "", "A", "A", ""),
    ("S07", "J1", 720, 1, "", "A", "A B", ""),
    ("S08", "J2", 676, 1, "", "B", "B", "D"),
    ("S09", "J2", 748, 1, "", "", "A", ""),
    ("S10", "J1", 731, 1, "", "", "A", ""),
    ("S11", "J1", 710, 1, "", "", "A B", "D"),
    ("S12", "J2", 705, 1, "", "", "A B", ""),
    ("S13", "J1", 690, 1, "", "", "B", ""),
    ("S14", "J2", 680, 1, "", "", "B", ""),
    ("S15", "J1", 650, 1, "", "", "C", ""),
    ("S16", "J2", 675, 1, "", "C", "A C", ""),
    ("S17", "J1", 675, 3, "", "", "C", ""),
    ("S18", "J2", 600, 1, "", "", "", "D"),
    ("S19", "J2", 675, 2, "", "", "A C", ""),
    ("S20", "J1", 599, 1, "", "", "", "D"),
]

PLANS = {**EARLY_PLANS, "4-D": {"seats": 3, "cutoff": 600}}


def build_candidates():
    candidates = []
    for candidate_id, junior, score, tie, first, second, third, fourth in SAMPLE_ROWS:
        candidates.append({
            "id": candidate_id,
            "junior": junior,
            "score": score,
            "tie": tie,
            "choice": first,
            "second_choices": second.split(),
            "choices": {3: third.split(), 4: fourth.split()},
        })
    return candidates


def run_general_batch(batch, candidates, plans, previous_admitted, previous_balances, *,
                      city_cutoff=CITY_CUTOFF, gradient_lines=GRADIENT_LINES,
                      choice_limit=6, ordered=None):
    balances = previous_balances.copy()
    admitted = {plan_id: [] for plan_id in plans if plan_id.startswith(f"{batch}-")}
    admitted_ids = {
        candidate_id
        for ids in previous_admitted.values()
        for candidate_id in ids
    }
    groups = [[] for _ in gradient_lines]
    log = []

    if ordered is None:
        ordered = sorted(candidates, key=lambda c: (-c["score"], c["tie"]))
    for candidate in ordered:
        candidate_id = candidate["id"]
        if not candidate["choices"][batch]:
            continue
        if candidate_id in admitted_ids:
            log.append(f"{candidate_id} 已在前批录取，跳过")
            continue
        if candidate["score"] < city_cutoff:
            log.append(f"{candidate_id} 低于全市控制线，跳过")
            continue
        for index, line in enumerate(gradient_lines):
            if candidate["score"] >= line:
                groups[index].append(candidate)
                break

    for gradient, group in enumerate(groups, start=1):
        for choice_index in range(choice_limit):
            for candidate in group:
                candidate_id = candidate["id"]
                choices = candidate["choices"][batch]
                if candidate_id in admitted_ids or choice_index >= len(choices):
                    continue

                school = choices[choice_index]
                plan_id = f"{batch}-{school}"
                prefix = f"G{gradient} 志愿{choice_index + 1} {candidate_id} {plan_id}"

                if plan_id not in admitted:
                    log.append(f"{prefix} 无对应计划")
                    continue
                cutoff = plans[plan_id].get("cutoff", city_cutoff)
                if candidate["score"] < cutoff:
                    log.append(f"{prefix} 低于计划控制线")
                elif balances[plan_id] == 0:
                    log.append(f"{prefix} 名额已满")
                else:
                    admitted[plan_id].append(candidate_id)
                    admitted_ids.add(candidate_id)
                    balances[plan_id] -= 1
                    log.append(f"{prefix} 已录取")

    return admitted, balances, log


def main():
    candidates = build_candidates()
    first, _, first_balances, _ = run_first_batch(
        candidates, first_batch_ranks, PLANS, city_cutoff=CITY_CUTOFF
    )
    second, second_balances, _, _ = run_second_batch(
        candidates, PLANS, first, first_balances
    )
    third, balances, log = run_general_batch(
        3, candidates, PLANS, {**first, **second}, second_balances
    )

    for label, result in (("第一批", first), ("第二批", second), ("第三批", third)):
        print(label, sum(len(ids) for ids in result.values()))

    print("\n第三批投档日志")
    for message in log:
        print(message)

    scores = {candidate["id"]: candidate["score"] for candidate in candidates}
    print("\n第三批各计划结果")
    for plan_id, ids in third.items():
        print(f"{plan_id} 录取顺序 {' -> '.join(ids) or '无'}")
        if ids:
            lowest = min(scores[candidate_id] for candidate_id in ids)
            last_id = ids[-1]
            print(f"最低分 {lowest}，末位 {last_id}（{scores[last_id]} 分）")
        else:
            print("最低分 无，末位 无")
        print(f"余额 {balances[plan_id]}")


if __name__ == "__main__":
    main()
