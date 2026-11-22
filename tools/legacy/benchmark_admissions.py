import argparse
import gc
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import tracemalloc
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

import admissions_app.core.runner as core
from admissions_app.datasets import load_dataset, summarize, validate_dataset
from admissions_app.core.second_batch import CITY_CUTOFF, GRADIENT_LINES

ROOT = Path(__file__).resolve().parents[2]


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def file_sha(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def source_hashes():
    names = ("admissions_app/core/first_batch.py", "admissions_app/core/second_batch.py", "admissions_app/core/third_batch.py",
             "admissions_app/core/runner.py", "admissions_app/datasets.py")
    values = {name: file_sha(ROOT / name) for name in names}
    values[Path(__file__).resolve().relative_to(ROOT).as_posix()] = file_sha(__file__)
    return values


def check_result(data, result):
    core.check_balances(data["plans"], result)
    by_id = {c["id"]: c for c in data["candidates"]}
    seen = set()
    for plan_id, ids in result["admitted"].items():
        parts = plan_id.split("-")
        batch, school = int(parts[0]), parts[1]
        if result["balances"][plan_id] < 0:
            raise ValueError(f"出现负余额：{plan_id}")
        for candidate_id in ids:
            if candidate_id in seen or candidate_id not in by_id:
                raise ValueError(f"重复或未知考生：{candidate_id}")
            seen.add(candidate_id)
            c = by_id[candidate_id]
            if c["score"] < max(CITY_CUTOFF, data["plans"][plan_id]["cutoff"]):
                raise ValueError(f"录取考生未达控制线：{candidate_id}")
            if batch == 1:
                valid = (c["choice"] == school and
                         candidate_id in data["autonomous_ranks"].get(school, {}))
            elif batch == 2:
                valid = c["junior"] == parts[2] and school in c["second_choices"]
            else:
                valid = school in c["choices"][batch]
            if not valid:
                raise ValueError(f"录取与资格或志愿不符：{candidate_id}")


def worker(args):
    manifest = json.loads((args.dataset / "manifest.json").read_text(encoding="utf-8"))
    seconds = {}

    def timed(label, function):
        def wrapped(*positional, **keywords):
            key = label or f"batch_{positional[0]}"
            start = perf_counter()
            value = function(*positional, **keywords)
            seconds[key] = perf_counter() - start
            return value
        return wrapped

    gc.collect()
    if args.trace:
        tracemalloc.start()
    start = perf_counter()
    data = load_dataset(args.dataset / "data.json")
    seconds["read_json"] = perf_counter() - start

    before = perf_counter()
    validate_dataset(data)
    counts = summarize(data)
    if data["config"] != manifest["config"] or counts != manifest["counts"]:
        raise ValueError("数据内容与清单不一致")
    seconds["dataset_validate"] = perf_counter() - before

    # 临时包装当前入口调用的函数，不复制四批业务流程。
    with patch.object(core, "validate_inputs", timed("validate_inputs", core.validate_inputs)), \
         patch.object(core, "run_first_batch", timed("batch_1", core.run_first_batch)), \
         patch.object(core, "run_second_batch", timed("batch_2", core.run_second_batch)), \
         patch.object(core, "run_general_batch", timed(None, core.run_general_batch)):
        before = perf_counter()
        result = core.run_all_batches(
            data["candidates"], data["plans"], ranks=data["autonomous_ranks"])
        seconds["admissions_total"] = perf_counter() - before

    before = perf_counter()
    check_result(data, result)
    seconds["result_validate"] = perf_counter() - before

    before = perf_counter()
    snapshot = {
        "candidates": data["candidates"], "plans": data["plans"],
        "autonomous_ranks": data["autonomous_ranks"],
        "city_cutoff": CITY_CUTOFF, "gradient_lines": GRADIENT_LINES,
        "choice_limits": core.CHOICE_LIMITS,
    }
    snapshot_bytes = encode(snapshot)
    seconds["input_serialize"] = perf_counter() - before
    before = perf_counter()
    result_bytes = encode(result)
    seconds["result_serialize"] = perf_counter() - before
    seconds["pipeline_total"] = perf_counter() - start

    memory = None
    if args.trace:
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        memory = {"current_bytes": current, "peak_bytes": peak}

    # 摘要及输出不在上述流水线和内存测量范围内。
    business = {key: result[key] for key in ("admitted", "balances")}
    business["transfers"] = sorted(result["transfers"])
    summary = {
        **counts,
        "batch_admitted": {
            str(batch): sum(len(ids) for plan, ids in result["admitted"].items()
                            if plan.startswith(f"{batch}-"))
            for batch in range(1, 5)
        },
        "admitted": sum(map(len, result["admitted"].values())),
        "remaining_seats": sum(result["balances"].values()),
        "transfer_rows": len(result["transfers"]),
        "log_rows": len(result["first_reasons"]) + sum(
            len(result[key]) for key in ("second_log", "third_log", "fourth_log")),
        "input_json_bytes": len(snapshot_bytes),
        "result_json_bytes": len(result_bytes),
    }
    if args.save_result:
        args.save_result.write_bytes(result_bytes)
    print(json.dumps({
        "seconds": seconds, "memory": memory, "summary": summary,
        "business_sha256": hashlib.sha256(encode(business)).hexdigest(),
        "full_result_sha256": hashlib.sha256(result_bytes).hexdigest(),
    }, ensure_ascii=False))


def git_text(*arguments):
    completed = subprocess.run(
        ["git", *arguments], cwd=ROOT, capture_output=True,
        text=True, encoding="utf-8", errors="replace", check=True)
    return completed.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description="现有四批算法的本地 JSON 性能基准")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--trace", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--save-result", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.dataset = args.dataset.resolve()
    if args.worker:
        worker(args)
        return
    if args.out is None or not 1 <= args.repeat <= 10:
        parser.error("请指定 --out，且 --repeat 在 1 至 10 之间")

    manifest_path = args.dataset / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if file_sha(args.dataset / "data.json") != manifest["data_sha256"]:
        raise ValueError("数据文件摘要与清单不一致")
    config = manifest["config"]
    if (config["city_cutoff"] != CITY_CUTOFF or
            config["gradient_lines"] != list(GRADIENT_LINES) or
            config["choice_limits"] != {str(k): v for k, v in core.CHOICE_LIMITS.items()}):
        raise ValueError("数据集规则与当前代码不一致")
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    sources = source_hashes()
    git_info = {
        "commit": git_text("rev-parse", "HEAD"),
        "tracked_changes": git_text("status", "--porcelain", "--untracked-files=no"),
    }

    def launch(trace=False, save=False):
        command = [sys.executable, "-B", str(Path(__file__).resolve()),
                   "--worker", "--dataset", str(args.dataset)]
        if trace:
            command.append("--trace")
        if save:
            command += ["--save-result", str(args.out / "baseline_result.json")]
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
        completed = subprocess.run(command, cwd=ROOT, env=environment,
                                   capture_output=True)
        if completed.returncode:
            raise RuntimeError(completed.stderr.decode("utf-8", errors="replace"))
        return json.loads(completed.stdout.decode("utf-8"))

    print("预跑 1 次……", flush=True)
    warmup = launch()
    rounds = []
    for index in range(args.repeat):
        print(f"正式计时 {index + 1}/{args.repeat}……", flush=True)
        row = launch(save=index == 0)
        rounds.append(row)
    print("独立测量 Python 内存……", flush=True)
    memory_round = launch(trace=True)

    for row in [*rounds, memory_round]:
        if any(row[key] != warmup[key] for key in
               ("summary", "business_sha256", "full_result_sha256")):
            raise ValueError("重复运行结果不一致")
    if sources != source_hashes() or file_sha(args.dataset / "data.json") != manifest["data_sha256"]:
        raise ValueError("实验期间源码或输入发生变化")

    aggregates = {}
    for stage in rounds[0]["seconds"]:
        values = [row["seconds"][stage] for row in rounds]
        aggregates[stage] = {
            "min": min(values), "median": statistics.median(values), "max": max(values)}
    report = {
        "format_version": "local-baseline-v1",
        "created_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "dataset_manifest": manifest, "source_sha256": sources, "git": git_info,
        "environment": {
            "python": sys.version, "executable": sys.executable,
            "platform": platform.platform(), "processor": platform.processor(),
            "logical_cpus": os.cpu_count(),
        },
        "conditions": {
            "fresh_process_each_round": True, "warmup_rounds": 1,
            "timing_rounds": args.repeat, "memory_rounds": 1,
            "file_cache": "未清空操作系统缓存；预检及重复读取会预热缓存",
            "timing": "perf_counter 墙钟秒数；不含进程启动、导入、摘要及结果文件写入",
            "memory": "单独一轮 tracemalloc；从读取到序列化结束的 Python 追踪分配峰值，非进程 RSS",
            "scope": "本地 JSON 与纯 Python 算法；未连接 MySQL",
        },
        "summary": warmup["summary"],
        "business_sha256": warmup["business_sha256"],
        "full_result_sha256": warmup["full_result_sha256"],
        "seconds": aggregates, "warmup_sample": warmup, "timing_samples": rounds,
        "memory_sample": memory_round,
    }
    (args.out / "benchmark.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n各阶段秒数：")
    for stage, values in aggregates.items():
        print(f"{stage:20s} 中位数 {values['median']:.6f} "
              f"最小 {values['min']:.6f} 最大 {values['max']:.6f}")
    print("分批录取：", report["summary"]["batch_admitted"])
    print("总录取：", report["summary"]["admitted"])
    print("日志条数：", report["summary"]["log_rows"])
    print("Python 追踪峰值 MiB：",
          round(memory_round["memory"]["peak_bytes"] / 1024**2, 2))
    print("重复运行、名额及录取约束核对通过")
    print("报告目录：", args.out)


if __name__ == "__main__":
    main()
