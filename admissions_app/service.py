"""统一应用服务；Web 与命令行共享相同事务、校验和推进逻辑。"""
import hashlib
import subprocess
import threading
from pathlib import Path

from .domain import advance, projection

ROOT = Path(__file__).resolve().parent.parent


def provenance():
    names = [ROOT / name for name in ("manage.py", "00_启动系统.bat", "database/schema_app.sql", "requirements.txt", "config/config.example.json")]
    names += sorted((ROOT / "admissions_app").rglob("*.py"))
    for directory, pattern in (("admissions_app/templates", "*.html"), ("admissions_app/static", "*"), ("tests", "*.py")):
        names += sorted((ROOT / directory).glob(pattern))
    names += [ROOT / "tools/legacy" / name for name in ("query_candidate.py", "report_admission_run.py")]
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        head = "未提供 Git 元数据"
    return {"git_head": head, "source_sha256": {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                                for p in names if p.exists()},
            "model_version": "app-v1", "layout_version": "package-layout-v2",
            "pre_layout_commit": "1424f2d",
            "historical_experiment_commit": "30ebf707c2a7180d3ede588812458da8e4d11ff5"}


class Service:
    def __init__(self, repository):
        self.repo = repository
        self.jobs = {}
        self.lock = threading.Lock()

    def execute(self, run_id, *, full=False, progress=None):
        data, rules, state = self.repo.load(run_id)
        ordered = sorted(data["candidates"], key=lambda c: (-c["score"], c["tie"]))
        count = 4 - state["completed_batch"] if full else 1
        if count <= 0 or state["completed_batch"] == 4:
            raise ValueError("四批已经完成")
        for _ in range(count):
            batch = state["completed_batch"]
            if progress:
                progress(f"正在计算第 {batch + 1} 批")
            updated = advance(data, rules, state, ordered=ordered)
            rows, plans = projection(data, rules, updated)
            if progress:
                progress(f"正在事务保存第 {batch + 1} 批")
            self.repo.save_step(run_id, batch, updated, rows, plans)
            state = updated
        return state

    def start(self, run_id, full=False):
        self.repo.run(run_id)
        with self.lock:
            if self.jobs.get(run_id, {}).get("active"):
                raise ValueError("该运行已有执行任务，请等待当前批次保存完成")
            self.jobs[run_id] = {"active": True, "message": "准备执行", "error": None}

        def progress(message):
            with self.lock:
                self.jobs[run_id]["message"] = message

        def work():
            try:
                self.execute(run_id, full=full, progress=progress)
                progress("本次执行已完成")
            except Exception as exc:
                # 不自动重复提交；提交响应丢失时，持久检查点才是恢复依据。
                with self.lock:
                    self.jobs[run_id]["error"] = f"{type(exc).__name__}：{str(exc)[:250]}。请刷新检查已提交批次，再核对或继续。"
            finally:
                with self.lock:
                    self.jobs[run_id]["active"] = False

        threading.Thread(target=work, name=f"admission-{run_id[:8]}", daemon=False).start()

    def status(self, run_id):
        with self.lock:
            job = dict(self.jobs.get(run_id, {"active": False, "message": "可从最近已提交批次继续", "error": None}))
        meta = self.repo.run(run_id)
        return {**job, "completed_batch": meta["completed_batch"], "admitted_count": meta["admitted_count"],
                "candidate_count": meta["candidate_count"], "remaining_seats": meta["remaining_seats"]}
