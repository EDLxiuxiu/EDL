candidates = [
    {"id": "S01", "score": 700, "choice": "A"},
    {"id": "S02", "score": 714, "choice": "A"},
    {"id": "S03", "score": 760, "choice": "A"},
    {"id": "S04", "score": 699, "choice": "A"},
    {"id": "S05", "score": 679, "choice": "B"},
]

# 只登记有资格的考生，字典里的数字是该校考核排名。
ranks = {
    "A": {"S01": 1, "S02": 2, "S04": 3},
    "B": {"S05": 1},
}

plans = {
    "1-A": {"seats": 1, "cutoff": 700},
    "1-B": {"seats": 1, "cutoff": 680},
    "3-A": {"seats": 1},
    "3-B": {"seats": 1},
}


def run_first_batch(candidates, ranks, plans, city_cutoff):
    balances = {plan_id: plan["seats"] for plan_id, plan in plans.items()}
    admitted = {plan_id: [] for plan_id in plans if plan_id.startswith("1-")}
    reasons = {}
    transfers = []

    by_school = {}
    for candidate in candidates:
        by_school.setdefault(candidate["choice"], []).append(candidate)

    for plan_id in admitted:
        school = plan_id.split("-", 1)[1]
        school_ranks = ranks.get(school, {})
        cutoff = max(city_cutoff, plans[plan_id]["cutoff"])
        eligible = []

        for candidate in by_school.get(school, []):
            candidate_id = candidate["id"]
            if candidate_id not in school_ranks:
                reasons[candidate_id] = "无该校自主招生资格"
            elif candidate["score"] < cutoff:
                reasons[candidate_id] = "低于控制线"
            else:
                eligible.append(candidate_id)

        eligible.sort(key=lambda candidate_id: school_ranks[candidate_id])
        for candidate_id in eligible:
            if balances[plan_id] == 0:
                reasons[candidate_id] = "计划名额已满"
                continue
            admitted[plan_id].append(candidate_id)
            balances[plan_id] -= 1
            reasons[candidate_id] = f"已录取至 {plan_id}"

    for source in admitted:
        seats_to_transfer = balances[source]
        if seats_to_transfer > 0:
            school = source.split("-", 1)[1]
            target = f"3-{school}"
            balances[source] -= seats_to_transfer
            balances[target] += seats_to_transfer
            transfers.append((source, target, seats_to_transfer))

    return admitted, reasons, balances, transfers


if __name__ == "__main__":
    admitted, reasons, balances, transfers = run_first_batch(
        candidates, ranks, plans, city_cutoff=600
    )

    print("第一批录取")
    for plan_id, candidate_ids in admitted.items():
        print(f"{plan_id} {'、'.join(candidate_ids) or '无'}")

    print("\n考生处理结果")
    for candidate_id in sorted(reasons):
        print(f"{candidate_id} {reasons[candidate_id]}")

    print("\n结转流水")
    if not transfers:
        print("无")
    for source, target, seats in transfers:
        print(f"{source} -> {target} {seats} 个名额")

    print("\n第一批结束后的计划余额")
    for plan_id, seats in balances.items():
        print(f"{plan_id} {seats}")
