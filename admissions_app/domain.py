"""规则、数据版本、分批执行和统计；不依赖 Flask 或数据库。"""
import copy
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass

from admissions_app.core.first_batch import ranks, run_first_batch
from admissions_app.core.runner import check_balances, validate_inputs
from admissions_app.core.second_batch import run_second_batch
from admissions_app.core.third_batch import PLANS, build_candidates, run_general_batch

LIMITS = {1: 1, 2: 3, 3: 6, 4: 6}
LOG_KEYS = {2: "second_log", 3: "third_log", 4: "fourth_log"}
CODE = re.compile(r"^[A-Za-z0-9_]{1,24}$")


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


def normalize(data):
    data = copy.deepcopy(data)
    for c in data["candidates"]:
        c["choices"] = {int(k): v for k, v in c["choices"].items()}
        c.setdefault("name", c["id"])
    return data


@dataclass(frozen=True)
class Rules:
    city_cutoff: int = 600
    first_gradient: int = 720
    interval: int = 40
    year: int = 2026

    def __post_init__(self):
        if any(type(v) is not int for v in asdict(self).values()):
            raise ValueError("规则参数必须为整数")
        if not 0 <= self.city_cutoff <= self.first_gradient <= 10000:
            raise ValueError("控制线须非负，且不高于第一梯度线")
        if self.interval <= 0 or not 2000 <= self.year <= 2100:
            raise ValueError("梯度间隔必须为正，年度须在 2000 至 2100 之间")
        if len(self.gradient_lines) > 1000:
            raise ValueError("梯度数量不能超过 1000")

    @property
    def gradient_lines(self):
        return tuple(range(self.first_gradient, self.city_cutoff, -self.interval)) + (self.city_cutoff,)


def sample_dataset():
    data = {
        "config": {"year": 2026, "description": "任务书 20 人固定样例"},
        "junior_schools": {"J1": "松林初中", "J2": "云溪初中"},
        "senior_schools": {s: {"name": n, "general_batch": b} for s, n, b in [
            ("A", "青禾高中", 3), ("B", "江湾高中", 3),
            ("C", "石桥高中", 3), ("D", "白鹭高中", 4)]},
        "candidates": build_candidates(), "plans": copy.deepcopy(PLANS),
        "autonomous_ranks": copy.deepcopy(ranks),
    }
    names = "方雨辰 何予安 叶以宁 许思源 郑沐阳 黄子晴 沈晨星 莫知行 温嘉文 吴舒言 林清悦 梁芷宁 陆欣桐 钟晓棠 周念慈 陈雨禾 张亦航 苏书远 杨秋然 罗若安".split()
    for c, name in zip(data["candidates"], names):
        c["name"] = name
    return normalize(data)


def validate(data, rules):
    try:
        validate_inputs(data["candidates"], data["plans"])
        juniors, seniors, plans = data["junior_schools"], data["senior_schools"], data["plans"]
        if not juniors or not seniors or not data["candidates"]:
            raise ValueError("学校及考生不能为空")
        for code in [*juniors, *seniors]:
            if not isinstance(code, str) or not CODE.fullmatch(code):
                raise ValueError("学校编号只允许 1 至 24 位字母、数字、下划线")
        for code, name in juniors.items():
            if not isinstance(name, str) or not name.strip() or len(name) > 100:
                raise ValueError(f"初中名称不能为空：{code}")
        for school, info in seniors.items():
            if type(info["general_batch"]) is not int or info["general_batch"] not in (3, 4) or not isinstance(info["name"], str) or not info["name"].strip() or len(info["name"]) > 100:
                raise ValueError(f"高中类型或名称不合法：{school}")
            if f"{info['general_batch']}-{school}" not in plans:
                raise ValueError(f"高中缺少统招计划：{school}")
        for pid, plan in plans.items():
            parts = pid.split("-")
            batch, school = int(parts[0]), parts[1]
            if len(pid) > 80 or len(parts) != (3 if batch == 2 else 2):
                raise ValueError(f"计划格式错误：{pid}")
            if school not in seniors or seniors[school]["general_batch"] != (4 if batch == 4 else 3):
                raise ValueError(f"计划高中或批次不匹配：{pid}")
            if batch == 2 and parts[2] not in juniors:
                raise ValueError(f"指标初中不存在：{pid}")
            if batch in (1, 2) and f"3-{school}" not in plans:
                raise ValueError(f"没有本校第三批结转目标：{pid}")
            if type(plan["seats"]) is not int or plan["seats"] < 0:
                raise ValueError(f"名额须为非负整数：{pid}")
            cutoff = plan.get("cutoff", rules.city_cutoff)
            if type(cutoff) is not int or cutoff < rules.city_cutoff:
                raise ValueError(f"计划控制线低于全市控制线：{pid}")
            if batch in (1, 2) and "cutoff" not in plan:
                raise ValueError(f"第一、第二批必须设置计划控制线：{pid}")
        ids = {c["id"] for c in data["candidates"]}
        for c in data["candidates"]:
            if not CODE.fullmatch(c["id"]) or c["junior"] not in juniors:
                raise ValueError(f"考生编号或生源初中不合法：{c['id']}")
            if not isinstance(c["name"], str) or not c["name"].strip() or len(c["name"]) > 100:
                raise ValueError("考生姓名不能为空")
            for school in c["second_choices"]:
                if f"2-{school}-{c['junior']}" not in plans:
                    raise ValueError(f"考生无该校本初中指标：{c['id']} {school}")
        for school, ranks_by_id in data["autonomous_ranks"].items():
            if f"1-{school}" not in plans:
                raise ValueError(f"资格学校无第一批计划：{school}")
            values = list(ranks_by_id.values())
            if any(type(v) is not int or v < 1 for v in values) or len(set(values)) != len(values):
                raise ValueError(f"考核排名须为校内唯一正整数：{school}")
            if not set(ranks_by_id) <= ids:
                raise ValueError(f"资格中有不存在的考生：{school}")
    except (KeyError, TypeError, IndexError, AttributeError) as exc:
        raise ValueError(f"输入字段缺失或类型错误：{exc}") from None


def initial_state(data):
    return {"completed_batch": 0, "admitted": {p: [] for p in data["plans"]},
            "balances": {p: v["seats"] for p, v in data["plans"].items()},
            "transfers": [], "first_reasons": {},
            "second_log": [], "third_log": [], "fourth_log": []}


def advance(data, rules, state, *, ordered=None):
    """纯函数式推进一批；保存失败时旧检查点仍可恢复。"""
    batch = state["completed_batch"] + 1
    if batch > 4:
        raise ValueError("四批已完成，不能再次结转或执行")
    state = copy.deepcopy(state)
    c, p = data["candidates"], data["plans"]
    if batch == 1:
        result, reasons, balances, transfers = run_first_batch(
            c, data["autonomous_ranks"], p, rules.city_cutoff)
        state["first_reasons"] = reasons
        state["transfers"].extend(transfers)
    elif batch == 2:
        result, balances, transfers, log = run_second_batch(
            c, p, state["admitted"], state["balances"],
            city_cutoff=rules.city_cutoff, gradient_lines=rules.gradient_lines, ordered=ordered)
        state["transfers"].extend(transfers)
        state["second_log"] = log
    else:
        result, balances, log = run_general_batch(
            batch, c, p, state["admitted"], state["balances"],
            city_cutoff=rules.city_cutoff, gradient_lines=rules.gradient_lines, ordered=ordered)
        state[LOG_KEYS[batch]] = log
    state["admitted"].update(result)
    state["balances"] = balances
    state["completed_batch"] = batch
    check_balances(p, state)
    admitted_ids = [cid for ids in state["admitted"].values() for cid in ids]
    if len(set(admitted_ids)) != len(admitted_ids):
        raise ValueError("出现重复录取")
    return state


def run_complete(data, rules):
    validate(data, rules)
    ordered = sorted(data["candidates"], key=lambda c: (-c["score"], c["tie"]))
    state = initial_state(data)
    for _ in range(4):
        state = advance(data, rules, state, ordered=ordered)
    return state


def choices(c, batch):
    return ([c["choice"]] if c["choice"] else []) if batch == 1 else (
        c["second_choices"] if batch == 2 else c["choices"][batch])


def verify_state(data, rules, state):
    validate(data, rules)
    if type(state["completed_batch"]) is not int or not 0 <= state["completed_batch"] <= 4:
        raise ValueError("检查点批次不合法")
    if set(state["admitted"]) != set(data["plans"]) or set(state["balances"]) != set(data["plans"]):
        raise ValueError("检查点计划集合不完整")
    check_balances(data["plans"], state)
    by_id = {c["id"]: c for c in data["candidates"]}
    seen = set()
    for pid, ids in state["admitted"].items():
        batch, school, *junior = pid.split("-")
        batch = int(batch)
        if ids and batch > state["completed_batch"]:
            raise ValueError("尚未执行的批次已有录取")
        if type(state["balances"][pid]) is not int or state["balances"][pid] < 0:
            raise ValueError("余额不合法")
        order_keys = []
        for cid in ids:
            if cid in seen or cid not in by_id:
                raise ValueError("重复或不存在的录取考生")
            seen.add(cid)
            c = by_id[cid]
            if school not in choices(c, batch) or c["score"] < max(rules.city_cutoff, data["plans"][pid].get("cutoff", rules.city_cutoff)):
                raise ValueError("录取不满足志愿或控制线")
            if batch == 1:
                rank = data["autonomous_ranks"].get(school, {}).get(cid)
                if rank is None:
                    raise ValueError("自主录取缺少资格")
                order_keys.append(rank)
            else:
                if batch == 2 and c["junior"] != junior[0]:
                    raise ValueError("指标跨初中使用")
                gradient = next(i for i, line in enumerate(rules.gradient_lines) if c["score"] >= line)
                order_keys.append((gradient, choices(c, batch).index(school), -c["score"], c["tie"]))
        if order_keys != sorted(order_keys):
            raise ValueError("计划内实际投档顺序错误")
    sources = set()
    for source, target, seats in state["transfers"]:
        parts = source.split("-")
        if source in sources or parts[0] not in ("1", "2") or int(parts[0]) > state["completed_batch"] or target != "3-" + parts[1] or type(seats) is not int or seats <= 0:
            raise ValueError("重复或非法结转")
        sources.add(source)


def projection(data, rules, state):
    """一次遍历日志，建立按考生索引；避免对每个考生扫描全量日志。"""
    by_id = {c["id"]: c for c in data["candidates"]}
    messages = defaultdict(lambda: defaultdict(list))
    events = defaultdict(list)
    incoming, outgoing = Counter(), Counter()
    for source, target, seats in state["transfers"]:
        outgoing[source] += seats
        incoming[target] += seats
    available = {pid: p["seats"] + incoming[pid] for pid, p in data["plans"].items()}
    first_orders = {cid: (pid, i) for pid, ids in state["admitted"].items() if pid.startswith("1-")
                    for i, cid in enumerate(ids, 1)}
    for cid, message in state["first_reasons"].items():
        messages[cid][1].append(message)
        pid = "1-" + by_id[cid]["choice"]
        before = (data["plans"][pid]["seats"] - first_orders[cid][1] + 1) if cid in first_orders else (0 if message == "计划名额已满" else None)
        events[cid].append({"batch": 1, "plan_id": pid, "choice_no": 1, "message": message,
                            "balance_before": before, "balance_after": before - 1 if cid in first_orders else before})
    for batch, key in LOG_KEYS.items():
        for line in state[key]:
            parts = line.split()
            decision = len(parts) >= 5 and parts[0].startswith("G") and parts[1].startswith("志愿")
            cid = parts[2] if decision else parts[0]
            if cid not in by_id:
                raise ValueError("处理日志未找到目标考生")
            messages[cid][batch].append(line)
            pid = parts[3] if decision else None
            before = available.get(pid)
            admitted = parts[-1] == "已录取"
            after = before - 1 if admitted and before is not None else before
            if after is not None and after < 0:
                raise ValueError("结构化事件出现负余额")
            if pid in available:
                available[pid] = after
            events[cid].append({"batch": batch, "plan_id": pid,
                                "choice_no": int(parts[1][2:]) if pid else None,
                                "message": line, "balance_before": before, "balance_after": after})
    where = {cid: (pid, i) for pid, ids in state["admitted"].items()
             for i, cid in enumerate(ids, 1)}
    rows = []
    for c in data["candidates"]:
        admission = where.get(c["id"])
        admitted_batch = int(admission[0].split("-")[0]) if admission else None
        trace = []
        for b in range(1, 5):
            lines = messages[c["id"]][b]
            if b > state["completed_batch"]:
                lines = ["本批尚未执行"]
            elif not lines:
                if admitted_batch and admitted_batch < b:
                    lines = ["已在前批录取，跳过本批"]
                elif not choices(c, b):
                    lines = ["本批未填志愿"]
                elif c["score"] < rules.city_cutoff:
                    lines = ["低于全市控制线"]
                else:
                    lines = ["该批日志未记录处理原因"]
            trace.append([b, choices(c, b), lines])
        rows.append({"candidate": c, "admission": list(admission) if admission else None,
                     "trace": trace, "batch": admitted_batch,
                     "plan_id": admission[0] if admission else None,
                     "admission_order": admission[1] if admission else None, "events": events[c["id"]]})
    plans = []
    for pid, p in sorted(data["plans"].items()):
        ids = state["admitted"][pid]
        batch, school, *junior = pid.split("-")
        last = by_id[ids[-1]] if ids else None
        plans.append({"plan_id": pid, "batch": int(batch), "school": school,
                      "junior": junior[0] if junior else None,
                      "initial_seats": p["seats"], "transferred_in": incoming[pid],
                      "transferred_out": outgoing[pid], "admitted_count": len(ids),
                      "remaining_seats": state["balances"][pid],
                      "lowest_score": min((by_id[c]["score"] for c in ids), default=None),
                      "last_id": last["id"] if last else None,
                      "last_score": last["score"] if last else None,
                      "last_choice": choices(last, int(batch)).index(school) + 1 if last else None,
                      "last_tie": last["tie"] if last else None})
    return rows, plans


def edit_entity(data, kind, key, value):
    """编辑产生新数据版本；从不原地改动历史快照。删除会再次验证引用。"""
    allowed = {"candidates", "plans", "junior_schools", "senior_schools", "autonomous_ranks"}
    if kind not in allowed:
        raise ValueError("未知数据类型")
    result = normalize(data)
    if kind == "candidates":
        found = [i for i, c in enumerate(result[kind]) if c["id"] == key]
        if value is None:
            if not found:
                raise ValueError("要删除的考生不存在")
            result[kind].pop(found[0])
        else:
            if value["id"] != key:
                raise ValueError("考生编号与编辑键不一致")
            value = normalize({"candidates": [value]})["candidates"][0]
            if found:
                result[kind][found[0]] = value
            else:
                result[kind].append(value)
    else:
        if value is None:
            if key not in result[kind]:
                raise ValueError("要删除的数据不存在")
            del result[kind][key]
        else:
            result[kind][key] = value
    return result
