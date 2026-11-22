from admissions_app.core.runner import check_balances, run_all_batches
from admissions_app.core.third_batch import PLANS, build_candidates


def run_case(target_id=None):
    # 每次重新创建 20 名考生及其志愿列表。
    candidates = build_candidates()

    if target_id is not None:
        target = next(candidate for candidate in candidates
                      if candidate["id"] == target_id)
        target["choices"][3] = ["B", "A"]

    result = run_all_batches(candidates, PLANS)
    check_balances(PLANS, result)
    return result


def show_case(title, result, focus_ids):
    admitted = result["admitted"]
    destination = {
        candidate_id: plan_id
        for plan_id, candidate_ids in admitted.items()
        for candidate_id in candidate_ids
    }

    print(f"\n=== {title} ===")
    for candidate_id in focus_ids:
        print(f"{candidate_id} 最终去向 {destination.get(candidate_id, '未录取')}")
    print(f"S13 最终去向 {destination.get('S13', '未录取')}")
    print("3-B 录取顺序", " -> ".join(admitted["3-B"]) or "无")
    print("4-D 录取顺序", " -> ".join(admitted["4-D"]) or "无")
    print(f"4-D 余额 {result['balances']['4-D']}")
    print(f"总录取 {len(destination)} 人")

    for label, key in (("第三批", "third_log"), ("第四批", "fourth_log")):
        for line in result[key]:
            words = line.split()
            if any(candidate_id in words for candidate_id in focus_ids):
                print(f"{label} {line}")


def main():
    base = run_case()
    case_a = run_case("S11")
    case_b = run_case("S12")

    show_case("基础样例", base, ("S11", "S12"))
    show_case("案例 A：只改 S11 的第三批志愿为 B → A", case_a, ("S11",))
    show_case("案例 B：只改 S12 的第三批志愿为 B → A", case_b, ("S12",))

    if run_case() != base:
        raise AssertionError("重跑基础样例时结果发生变化")
    print("\n重跑基础样例，结果一致")


if __name__ == "__main__":
    main()