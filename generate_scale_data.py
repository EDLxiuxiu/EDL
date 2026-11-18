import argparse
import hashlib
import json
import platform
import random
import sys
from collections import Counter
from pathlib import Path
from time import perf_counter

from run_admissions import CHOICE_LIMITS, validate_inputs
from second_batch import CITY_CUTOFF, GRADIENT_LINES

VERSION = "scale-v1"


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def split_seats(total, codes):
    base, extra = divmod(total, len(codes))
    return {code: base + (i < extra) for i, code in enumerate(codes)}


def build_dataset(config):
    rng = random.Random(config["seed"])
    juniors = [f"J{i:03d}" for i in range(1, config["juniors"] + 1)]
    third = [f"H{i:03d}" for i in range(1, config["third_schools"] + 1)]
    fourth = [f"H{i:03d}" for i in range(len(third) + 1,
                                       len(third) + config["fourth_schools"] + 1)]
    total = config["count"] * config["seat_percent"] // 100
    third_total = total * config["third_seat_percent"] // 100
    school_seats = {**split_seats(third_total, third),
                    **split_seats(total - third_total, fourth)}
    plans, seniors = {}, {}
    eligible = {junior: [] for junior in juniors}
    for school in third + fourth:
        seats = school_seats[school]
        batch = 3 if school in third else 4
        seniors[school] = {"name": f"模拟高中{school}",
                           "general_batch": batch, "total_seats": seats}
        if batch == 4:
            plans[f"4-{school}"] = {"seats": seats, "cutoff": CITY_CUTOFF}
            continue
        first, second = seats * 10 // 100, seats * 50 // 100
        plans[f"1-{school}"] = {"seats": first, "cutoff": CITY_CUTOFF + 80}
        plans[f"3-{school}"] = {"seats": seats - first - second,
                                "cutoff": CITY_CUTOFF}
        recipients = juniors.copy()
        rng.shuffle(recipients)
        for junior, quota in split_seats(second, recipients).items():
            if quota:
                plans[f"2-{school}-{junior}"] = {
                    "seats": quota,
                    "cutoff": CITY_CUTOFF + (40 if int(school[1:]) % 2 else 0),
                }
                eligible[junior].append(school)

    def choices(pool, limit):
        length = rng.randint(0, min(limit, len(pool)))
        selected = rng.sample(pool, length)
        if length and rng.randrange(100) < config["hot_percent"]:
            first = rng.choice(pool[:max(1, len(pool) // 5)])
            if first in selected:
                selected.remove(first)
            else:
                selected.pop()
            selected.insert(0, first)
        return selected

    assignments = [juniors[i % len(juniors)] for i in range(config["count"])]
    rng.shuffle(assignments)
    ties, candidates = Counter(), []
    qualified = {school: [] for school in third}
    for i, junior in enumerate(assignments, start=1):
        candidate_id = f"S{i:06d}"
        score = max(350, min(800, round(rng.gauss(config["score_mean"],
                                                config["score_sd"]))))
        ties[score] += 1
        first = rng.choice(third) if rng.randrange(100) < config["first_percent"] else ""
        candidates.append({
            "id": candidate_id, "name": f"模拟考生{i:06d}",
            "junior": junior, "score": score, "tie": ties[score],
            "choice": first, "second_choices": choices(eligible[junior], 3),
            "choices": {3: choices(third, 6), 4: choices(fourth, 6)},
        })
        if first and rng.randrange(100) < config["qualified_percent"]:
            qualified[first].append(candidate_id)
    ranks = {}
    for school, ids in qualified.items():
        rng.shuffle(ids)
        ranks[school] = {candidate_id: rank for rank, candidate_id in enumerate(ids, 1)}
    return {
        "config": config,
        "junior_schools": {code: f"模拟初中{code}" for code in juniors},
        "senior_schools": seniors, "candidates": candidates,
        "plans": dict(sorted(plans.items())), "autonomous_ranks": ranks,
    }


def validate_dataset(data):
    candidates, plans, config = data["candidates"], data["plans"], data["config"]
    validate_inputs(candidates, plans)
    if len(candidates) != config["count"]:
        raise ValueError("考生人数与配置不一致")
    juniors, seniors = data["junior_schools"], data["senior_schools"]
    if len(juniors) != config["juniors"] or len(seniors) != (
            config["third_schools"] + config["fourth_schools"]):
        raise ValueError("学校数量与配置不一致")
    totals, batch_seats = Counter(), Counter()
    for plan_id, plan in plans.items():
        parts = plan_id.split("-")
        batch, school = int(parts[0]), parts[1]
        if school not in seniors or batch not in (1, 2, 3, 4):
            raise ValueError(f"非法计划：{plan_id}")
        expected = 3 if batch in (1, 2, 3) else 4
        if seniors[school]["general_batch"] != expected:
            raise ValueError(f"学校批次冲突：{plan_id}")
        if len(parts) != (3 if batch == 2 else 2):
            raise ValueError(f"计划编号格式错误：{plan_id}")
        if batch == 2 and parts[2] not in juniors:
            raise ValueError(f"第二批初中不存在：{plan_id}")
        if type(plan["seats"]) is not int or plan["seats"] < 0:
            raise ValueError(f"非法名额：{plan_id}")
        if plan["cutoff"] < CITY_CUTOFF:
            raise ValueError(f"计划控制线过低：{plan_id}")
        totals[school] += plan["seats"]
        batch_seats[school, batch] += plan["seats"]
    for school, info in seniors.items():
        total = info["total_seats"]
        if totals[school] != total:
            raise ValueError(f"学校总名额不平：{school}")
        if info["general_batch"] == 3:
            if (batch_seats[school, 1], batch_seats[school, 2]) != (
                    total * 10 // 100, total * 50 // 100):
                raise ValueError(f"第一、二批切分错误：{school}")
    if sum(totals.values()) != config["count"] * config["seat_percent"] // 100:
        raise ValueError("全市总名额不平")
    by_id = {c["id"]: c for c in candidates}
    for c in candidates:
        if c["junior"] not in juniors or not 350 <= c["score"] <= 800:
            raise ValueError(f"考生数据错误：{c['id']}")
        for school in c["second_choices"]:
            if f"2-{school}-{c['junior']}" not in plans:
                raise ValueError(f"无对应名额分配子计划：{c['id']}")
    for school, ranks in data["autonomous_ranks"].items():
        if f"1-{school}" not in plans:
            raise ValueError(f"资格学校不存在：{school}")
        if set(ranks.values()) != set(range(1, len(ranks) + 1)):
            raise ValueError(f"校内考核排名不唯一或不连续：{school}")
        for candidate_id in ranks:
            if candidate_id not in by_id or by_id[candidate_id]["choice"] != school:
                raise ValueError(f"资格与考生不匹配：{candidate_id}")


def summarize(data):
    choice_rows = sum(bool(c["choice"]) + len(c["second_choices"]) +
                      len(c["choices"][3]) + len(c["choices"][4])
                      for c in data["candidates"])
    return {
        "candidates": len(data["candidates"]),
        "juniors": len(data["junior_schools"]),
        "seniors": len(data["senior_schools"]), "plans": len(data["plans"]),
        "initial_seats": sum(p["seats"] for p in data["plans"].values()),
        "choice_rows": choice_rows,
        "qualified_rows": sum(len(r) for r in data["autonomous_ranks"].values()),
    }


def load_dataset(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    # JSON 对象键会变成字符串，恢复现有投档函数需要的整数批次键。
    for candidate in data["candidates"]:
        candidate["choices"] = {int(k): v for k, v in candidate["choices"].items()}
    return data


def verify_directory(directory):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    with (directory / "data.json").open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != manifest["data_sha256"]:
        raise ValueError("数据文件 SHA256 不一致")
    data = load_dataset(directory / "data.json")
    validate_dataset(data)
    if summarize(data) != manifest["counts"] or data["config"] != manifest["config"]:
        raise ValueError("数据与清单不一致")
    print("文件校验通过；SHA256：", digest)
    print(json.dumps(manifest["counts"], ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="生成独立、可复现的虚构规模数据，不连接数据库")
    parser.add_argument("--verify", type=Path, help="仅校验已有数据目录")
    parser.add_argument("--out", type=Path, help="新输出目录，已存在则拒绝覆盖")
    for name, default in {
        "count": 10000, "seed": 20261118, "juniors": 500,
        "third-schools": 100, "fourth-schools": 50,
        "seat-percent": 80, "third-seat-percent": 80,
        "score-mean": 660, "score-sd": 70,
        "first-percent": 15, "qualified-percent": 70, "hot-percent": 60,
    }.items():
        parser.add_argument(f"--{name}", type=int, default=default)
    args = parser.parse_args()
    if args.verify:
        verify_directory(args.verify)
        return
    if args.out is None:
        parser.error("生成时必须指定 --out")
    config = {key: value for key, value in vars(args).items() if key not in ("out", "verify")}
    if not 10000 <= args.count <= 150000:
        parser.error("考生数应在 10000 至 150000 之间")
    if not 1 <= args.juniors <= args.count:
        parser.error("初中数量须在 1 与考生数之间")
    if not 1 <= args.third_schools <= 500 or not 1 <= args.fourth_schools <= 500:
        parser.error("第三、四批高中数量各须在 1 至 500 之间")
    if args.score_sd <= 0 or not 350 <= args.score_mean <= 800:
        parser.error("分数均值须在 350 至 800 之间，标准差须大于 0")
    if any(not 0 <= v <= 100 for k, v in config.items() if k.endswith("_percent")):
        parser.error("百分比参数须在 0 至 100 之间")
    config.update(generator_version=VERSION, year=2026,
                  city_cutoff=CITY_CUTOFF, gradient_lines=list(GRADIENT_LINES),
                  choice_limits={str(k): v for k, v in CHOICE_LIMITS.items()})
    config_id = hashlib.sha256(json_bytes(config)).hexdigest()[:16]
    args.out.mkdir(parents=True, exist_ok=False)
    t0 = perf_counter()
    data = build_dataset(config)
    t1 = perf_counter()
    validate_dataset(data)
    counts = summarize(data)
    t2 = perf_counter()
    payload = json_bytes(data)
    t3 = perf_counter()
    (args.out / "data.json").write_bytes(payload)
    t4 = perf_counter()
    digest = hashlib.sha256(payload).hexdigest()
    manifest = {
        "dataset_id": f"{VERSION}-{args.count}-{config_id}",
        "description": "教学用虚构数据，不代表真实招生分布",
        "config": config, "counts": counts, "data_sha256": digest,
        "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "environment": {"python": sys.version, "platform": platform.platform()},
        "seconds": {"generate": t1 - t0, "validate_and_summarize": t2 - t1,
                    "serialize": t3 - t2, "file_write": t4 - t3},
        "data_bytes": len(payload),
    }
    # 清单最后写入；耗时不参与 data.json 的摘要，便于重复生成对照。
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("生成与输入校验通过：", args.out.resolve())
    print("数据集：", manifest["dataset_id"])
    print("SHA256：", digest)
    print(json.dumps(counts, ensure_ascii=False, indent=2))
    print("分阶段秒数：", manifest["seconds"])


if __name__ == "__main__":
    main()