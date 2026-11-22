"""MySQL 持久化与明确标注的内存演示存储，共享同一个业务接口。"""
import copy
import json
import threading
import zlib
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from uuid import uuid4
from pathlib import Path

from .domain import Rules, choices, digest, encode, initial_state, normalize, projection, validate, verify_state

PLAN_FIELDS = ("plan_id", "batch", "school", "junior", "initial_seats", "transferred_in",
               "transferred_out", "admitted_count", "remaining_seats", "lowest_score",
               "last_id", "last_score", "last_choice", "last_tie")
CANDIDATE_FIELDS = ("candidate_no", "name", "junior_code", "score", "tie_no", "admitted_batch",
                    "senior_code", "plan_id", "admission_order", "choice_no")


def connection_settings():
    settings = {"host": "127.0.0.1", "port": 3306, "user": "admission_app", "database": "admissionsim_app"}
    path = Path(__file__).resolve().parent.parent / "config.local.json"
    if path.exists():
        supplied = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(supplied, dict) or set(supplied) - settings.keys():
            raise ValueError("配置只支持 host、port、user、database，密码不能写入配置")
        settings.update(supplied)
    if settings["host"] not in ("127.0.0.1", "localhost") or settings["database"] != "admissionsim_app":
        raise ValueError("主系统仅支持本机独立主库 admissionsim_app")
    if type(settings["port"]) is not int or not 1 <= settings["port"] <= 65535:
        raise ValueError("数据库端口不合法")
    return settings


def packed(value):
    return zlib.compress(encode(value))


def unpacked(value):
    return json.loads(zlib.decompress(bytes(value)))


def candidate_record(row):
    c, pid = row["candidate"], row["plan_id"]
    batch = row["batch"]
    return dict(zip(CANDIDATE_FIELDS, (
        c["id"], c["name"], c["junior"], c["score"], c["tie"], batch,
        pid.split("-")[1] if pid else None, pid, row["admission_order"],
        choices(c, batch).index(pid.split("-")[1]) + 1 if pid else None)))


class Conflict(ValueError):
    pass


class MySQLRepository:
    mode = "MySQL 持久化"

    def __init__(self, password, *, database="admissionsim_app", user=None):
        if database != "admissionsim_app":
            raise ValueError("主系统只能写入 admissionsim_app，历史实验库不允许作为写入目标")
        self.password, self.database, self.user = password, database, user

    def connect(self):
        import mysql.connector
        settings = connection_settings()
        settings["database"] = self.database
        if self.user is not None:
            settings["user"] = self.user
        return mysql.connector.connect(**settings, password=self.password,
                                       charset="utf8mb4", use_pure=True, autocommit=False,
                                       connection_timeout=10)

    @contextmanager
    def transaction(self, *, readonly=False):
        with self.connect() as con:
            con.start_transaction(isolation_level="REPEATABLE READ",
                                  consistent_snapshot=True, readonly=readonly)
            try:
                with con.cursor(dictionary=True) as cur:
                    yield cur
                if readonly:
                    con.rollback()
                else:
                    con.commit()
            except BaseException:
                con.rollback()
                raise

    def datasets(self):
        with self.transaction(readonly=True) as c:
            c.execute("SELECT dataset_id,parent_id,label,content_sha,candidate_count,created_at "
                      "FROM app_datasets ORDER BY created_at DESC")
            return c.fetchall()

    def dataset(self, dataset_id):
        with self.transaction(readonly=True) as c:
            c.execute("SELECT payload,content_sha FROM app_datasets WHERE dataset_id=%s", (dataset_id,))
            row = c.fetchone()
        if not row:
            raise ValueError("数据版本不存在")
        data = normalize(unpacked(row["payload"]))
        if digest(data) != row["content_sha"]:
            raise ValueError("数据版本内容摘要不一致")
        return data

    def put_dataset(self, data, label, parent_id=None, rules=None, dataset_id=None):
        data = normalize(data)
        validate(data, rules or Rules(city_cutoff=0, first_gradient=0))
        if not label.strip() or len(label) > 200:
            raise ValueError("数据版本名称须为 1 至 200 个字符")
        identifier = dataset_id or str(uuid4())
        with self.transaction() as c:
            c.execute("INSERT INTO app_datasets "
                      "(dataset_id,parent_id,label,content_sha,candidate_count,payload) VALUES (%s,%s,%s,%s,%s,%s)",
                      (identifier, parent_id, label, digest(data), len(data["candidates"]), packed(data)))
        return identifier

    def runs(self):
        with self.transaction(readonly=True) as c:
            c.execute("SELECT run_id,dataset_id,completed_batch,candidate_count,admitted_count,remaining_seats,"
                      "created_at,updated_at FROM app_runs ORDER BY created_at DESC")
            return c.fetchall()

    def run(self, run_id):
        with self.transaction(readonly=True) as c:
            c.execute("SELECT * FROM app_runs WHERE run_id=%s", (run_id,))
            row = c.fetchone()
        if not row:
            raise ValueError("运行不存在")
        row["rules"] = json.loads(row["rules"])
        row["provenance"] = json.loads(row["provenance"])
        return row

    def load(self, run_id):
        # 元数据和检查点必须来自同一个一致性快照。
        with self.transaction(readonly=True) as c:
            c.execute("SELECT r.rules,r.completed_batch,d.payload,d.content_sha,k.payload AS state,"
                      "k.state_sha FROM app_runs r JOIN app_datasets d ON r.dataset_id=d.dataset_id "
                      "JOIN app_checkpoints k ON k.run_id=r.run_id AND k.batch_no=r.completed_batch "
                      "WHERE r.run_id=%s", (run_id,))
            row = c.fetchone()
        if not row:
            raise ValueError("运行或检查点不存在")
        data, state = normalize(unpacked(row["payload"])), unpacked(row["state"])
        if digest(data) != row["content_sha"] or digest(state) != row["state_sha"]:
            raise ValueError("快照或检查点摘要不一致")
        return data, Rules(**json.loads(row["rules"])), state

    def create_run(self, dataset_id, rules, provenance, run_id=None):
        data = self.dataset(dataset_id)
        validate(data, rules)
        state = initial_state(data)
        candidates, plans = projection(data, rules, state)
        identifier = run_id or str(uuid4())
        with self.transaction() as c:
            c.execute("INSERT INTO app_runs (run_id,dataset_id,rules,provenance,candidate_count,remaining_seats) "
                      "VALUES (%s,%s,%s,%s,%s,%s)", (identifier, dataset_id, encode(asdict(rules)).decode(),
                      encode(provenance).decode(), len(data["candidates"]), sum(state["balances"].values())))
            self._save(c, identifier, state, candidates, plans)
        return identifier

    @staticmethod
    def _chunks(c, sql, rows):
        for start in range(0, len(rows), 500):
            c.executemany(sql, rows[start:start + 500])

    def _save(self, c, run_id, state, candidates, plans):
        fields = ("run_id", *PLAN_FIELDS)
        self._chunks(c, f"INSERT INTO app_plan_results ({','.join(fields)}) "
                     f"VALUES ({','.join(['%s'] * len(fields))}) ON DUPLICATE KEY UPDATE " +
                     ",".join(f"{k}=VALUES({k})" for k in PLAN_FIELDS[1:]),
                     [(run_id, *(p[k] for k in PLAN_FIELDS)) for p in plans])
        fields = ("run_id", *CANDIDATE_FIELDS, "payload")
        self._chunks(c, f"INSERT INTO app_candidate_results ({','.join(fields)}) "
                     f"VALUES ({','.join(['%s'] * len(fields))}) ON DUPLICATE KEY UPDATE " +
                     ",".join(f"{k}=VALUES({k})" for k in (*CANDIDATE_FIELDS[1:], "payload")),
                     [(run_id, *candidate_record(row).values(), encode(row).decode()) for row in candidates])
        # 各阶段来源不同，按固定序号幂等插入；禁止改变已存在流水。
        c.execute("SELECT COUNT(*) AS n FROM app_transfers WHERE run_id=%s", (run_id,))
        previous = c.fetchone()["n"]
        self._chunks(c, "INSERT INTO app_transfers VALUES (%s,%s,%s,%s,%s)",
                     [(run_id, i, *row) for i, row in enumerate(state["transfers"], 1) if i > previous])
        c.execute("INSERT INTO app_checkpoints (run_id,batch_no,state_sha,payload) VALUES (%s,%s,%s,%s)",
                  (run_id, state["completed_batch"], digest(state), packed(state)))

    def save_step(self, run_id, expected_batch, state, candidates, plans):
        if state["completed_batch"] != expected_batch + 1:
            raise ValueError("检查点必须顺序推进一批")
        with self.transaction() as c:
            c.execute("UPDATE app_runs SET completed_batch=%s,admitted_count=%s,remaining_seats=%s "
                      "WHERE run_id=%s AND completed_batch=%s", (state["completed_batch"],
                      sum(p["admitted_count"] for p in plans), sum(state["balances"].values()), run_id, expected_batch))
            if c.rowcount != 1:
                raise Conflict("运行已由另一请求推进，请刷新；没有重复保存或结转")
            self._save(c, run_id, state, candidates, plans)

    @staticmethod
    def _where(run_id, filters, *, plan=False):
        clauses, args = ["run_id=%s"], [run_id]
        fields = {"school": "school", "junior": "junior", "batch": "batch"} if plan else {
            "school": "senior_code", "junior": "junior_code", "batch": "admitted_batch", "plan": "plan_id"}
        for key, field in fields.items():
            if filters.get(key):
                clauses.append(field + "=%s")
                args.append(filters[key])
        if not plan and filters.get("status") in ("admitted", "unadmitted"):
            clauses.append("admitted_batch IS " + ("NOT NULL" if filters["status"] == "admitted" else "NULL"))
        if not plan and filters.get("q"):
            clauses.append("(LOCATE(%s,candidate_no)>0 OR LOCATE(%s,name)>0)")
            args.extend([filters["q"]] * 2)
        return " AND ".join(clauses), args

    def candidates(self, run_id, filters=None, page=1, limit=50):
        where, args = self._where(run_id, filters or {})
        with self.transaction(readonly=True) as c:
            c.execute("SELECT COUNT(*) AS n FROM app_candidate_results WHERE " + where, args)
            total = c.fetchone()["n"]
            c.execute("SELECT " + ",".join(CANDIDATE_FIELDS) + " FROM app_candidate_results WHERE " +
                      where + " ORDER BY plan_id,admission_order,candidate_no LIMIT %s OFFSET %s",
                      [*args, limit, (page - 1) * limit])
            return c.fetchall(), total

    def candidate(self, run_id, candidate_no):
        with self.transaction(readonly=True) as c:
            c.execute("SELECT payload FROM app_candidate_results WHERE run_id=%s AND candidate_no=%s",
                      (run_id, candidate_no))
            row = c.fetchone()
        if not row:
            raise ValueError("该运行中不存在此考生")
        return json.loads(row["payload"])

    def plans(self, run_id, filters=None, page=1, limit=50):
        where, args = self._where(run_id, filters or {}, plan=True)
        with self.transaction(readonly=True) as c:
            c.execute("SELECT COUNT(*) AS n FROM app_plan_results WHERE " + where, args)
            total = c.fetchone()["n"]
            c.execute("SELECT " + ",".join(PLAN_FIELDS) + " FROM app_plan_results WHERE " +
                      where + " ORDER BY plan_id LIMIT %s OFFSET %s", [*args, limit, (page - 1) * limit])
            return c.fetchall(), total

    def overview(self, run_id):
        with self.transaction(readonly=True) as c:
            c.execute("SELECT admitted_batch AS batch,COUNT(*) AS count FROM app_candidate_results "
                      "WHERE run_id=%s GROUP BY admitted_batch ORDER BY admitted_batch", (run_id,))
            batches = c.fetchall()
            c.execute("SELECT FLOOR(score/50)*50 AS lower_score,COUNT(*) AS candidate_count,"
                      "SUM(admitted_batch IS NOT NULL) AS admitted_count FROM app_candidate_results FORCE INDEX (ix_candidate_statistics) "
                      "WHERE run_id=%s GROUP BY FLOOR(score/50)*50 ORDER BY lower_score", (run_id,))
            scores = c.fetchall()
            c.execute("SELECT junior_code,COUNT(*) AS candidate_count,SUM(admitted_batch IS NOT NULL) "
                      "AS admitted_count FROM app_candidate_results FORCE INDEX (ix_candidate_statistics) WHERE run_id=%s GROUP BY junior_code "
                      "ORDER BY junior_code", (run_id,))
            juniors = c.fetchall()
            c.execute("SELECT school,SUM(initial_seats) AS initial_seats,SUM(admitted_count) AS admitted_count,"
                      "SUM(remaining_seats) AS remaining_seats FROM app_plan_results WHERE run_id=%s "
                      "GROUP BY school ORDER BY school", (run_id,))
            return {"batches": batches, "scores": scores, "juniors": juniors, "schools": c.fetchall()}

    def transfers(self, run_id):
        with self.transaction(readonly=True) as c:
            c.execute("SELECT transfer_no,source_plan_id,target_plan_id,seats FROM app_transfers "
                      "WHERE run_id=%s ORDER BY transfer_no", (run_id,))
            return c.fetchall()

    def iter_export(self, run_id, kind, filters, expected_batch):
        with self.transaction(readonly=True) as c:
            c.execute("SELECT completed_batch FROM app_runs WHERE run_id=%s", (run_id,))
            if c.fetchone()["completed_batch"] != expected_batch:
                raise Conflict("运行已推进，请重新导出")
            if kind == "transfers":
                c.execute("SELECT transfer_no,source_plan_id,target_plan_id,seats FROM app_transfers WHERE run_id=%s ORDER BY transfer_no", (run_id,))
            else:
                plan = kind == "plans"
                fields = PLAN_FIELDS if plan else CANDIDATE_FIELDS
                where, args = self._where(run_id, filters, plan=plan)
                c.execute("SELECT " + ",".join(fields) + " FROM " +
                          ("app_plan_results" if plan else "app_candidate_results") + " WHERE " + where +
                          (" ORDER BY plan_id" if plan else " ORDER BY plan_id,admission_order,candidate_no"), args)
            yield from c

    def audit(self, run_id):
        # 冗余全量核对固定为一个一致性快照，检查点加载后检测并发推进。
        data, rules, state = self.load(run_id)
        verify_state(data, rules, state)
        rows, plans = projection(data, rules, state)
        with self.transaction(readonly=True) as c:
            c.execute("SELECT completed_batch,admitted_count,remaining_seats FROM app_runs WHERE run_id=%s", (run_id,))
            meta = c.fetchone()
            if meta["completed_batch"] != state["completed_batch"]:
                raise Conflict("核对期间运行已推进，请重试")
            if meta["admitted_count"] != sum(p["admitted_count"] for p in plans) or meta["remaining_seats"] != sum(state["balances"].values()):
                raise ValueError("运行汇总冗余不一致")
            c.execute("SELECT " + ",".join(PLAN_FIELDS) + " FROM app_plan_results WHERE run_id=%s ORDER BY plan_id", (run_id,))
            if c.fetchall() != plans:
                raise ValueError("逐计划冗余与检查点不一致")
            expected = {row["candidate"]["id"]: row for row in rows}
            c.execute("SELECT * FROM app_candidate_results WHERE run_id=%s", (run_id,))
            count = 0
            for row in c:
                wanted = expected.get(row["candidate_no"])
                if wanted is None or json.loads(row["payload"]) != json.loads(encode(wanted)):
                    raise ValueError("考生快照或过程冗余不一致")
                if any(row[k] != v for k, v in candidate_record(wanted).items()):
                    raise ValueError("考生结构化状态不一致")
                count += 1
            if count != len(expected):
                raise ValueError("考生投影记录不完整")
            c.execute("SELECT source_plan_id,target_plan_id,seats FROM app_transfers WHERE run_id=%s ORDER BY transfer_no", (run_id,))
            observed = [[r["source_plan_id"], r["target_plan_id"], r["seats"]] for r in c]
            if observed != [list(t) for t in state["transfers"]]:
                raise ValueError("结转流水不一致")
        return {"status": "verified", "candidates": count, "plans": len(plans),
                "transfers": len(state["transfers"]), "completed_batch": state["completed_batch"]}


class MemoryRepository:
    """只用于无密码演示和测试；重启丢失，不作为 MySQL 测量。"""
    mode = "内存演示，重启后清空"

    def __init__(self):
        self._datasets, self._runs = {}, {}
        self.lock = threading.RLock()

    def put_dataset(self, data, label, parent_id=None, rules=None, dataset_id=None):
        data = normalize(data)
        validate(data, rules or Rules(city_cutoff=0, first_gradient=0))
        if not label.strip() or len(label) > 200:
            raise ValueError("数据版本名称须为 1 至 200 个字符")
        if parent_id and parent_id not in self._datasets:
            raise ValueError("父版本不存在")
        identifier = dataset_id or str(uuid4())
        self._datasets[identifier] = {"dataset_id": identifier, "parent_id": parent_id,
                                     "label": label, "content_sha": digest(data),
                                     "candidate_count": len(data["candidates"]),
                                     "created_at": datetime.now(), "data": copy.deepcopy(data)}
        return identifier

    def datasets(self):
        return [{k: v for k, v in row.items() if k != "data"} for row in reversed(list(self._datasets.values()))]

    def dataset(self, dataset_id):
        if dataset_id not in self._datasets:
            raise ValueError("数据版本不存在")
        return copy.deepcopy(self._datasets[dataset_id]["data"])

    def create_run(self, dataset_id, rules, provenance, run_id=None):
        data = self.dataset(dataset_id)
        validate(data, rules)
        state = initial_state(data)
        identifier = run_id or str(uuid4())
        rows, plans = projection(data, rules, state)
        self._runs[identifier] = {"run_id": identifier, "dataset_id": dataset_id, "rules": asdict(rules),
                                  "provenance": provenance, "completed_batch": 0,
                                  "candidate_count": len(data["candidates"]), "admitted_count": 0,
                                  "remaining_seats": sum(state["balances"].values()),
                                  "created_at": datetime.now(), "updated_at": datetime.now(),
                                  "state": state, "candidates": rows, "plans": plans}
        return identifier

    def run(self, run_id):
        if run_id not in self._runs:
            raise ValueError("运行不存在")
        return copy.deepcopy({k: v for k, v in self._runs[run_id].items() if k not in {"state", "candidates", "plans"}})

    def runs(self):
        return [self.run(k) for k in reversed(self._runs)]

    def load(self, run_id):
        with self.lock:
            row = self.run(run_id)
            return self.dataset(row["dataset_id"]), Rules(**row["rules"]), copy.deepcopy(self._runs[run_id]["state"])

    def save_step(self, run_id, expected_batch, state, candidates, plans):
        with self.lock:
            row = self._runs[run_id]
            if row["completed_batch"] != expected_batch:
                raise Conflict("运行已推进，请刷新")
            if state["completed_batch"] != expected_batch + 1:
                raise ValueError("检查点必须顺序推进一批")
            row.update(state=copy.deepcopy(state), candidates=copy.deepcopy(candidates), plans=copy.deepcopy(plans),
                       completed_batch=state["completed_batch"], admitted_count=sum(p["admitted_count"] for p in plans),
                       remaining_seats=sum(state["balances"].values()), updated_at=datetime.now())

    def candidate(self, run_id, candidate_no):
        for row in self._runs[run_id]["candidates"]:
            if row["candidate"]["id"] == candidate_no:
                return copy.deepcopy(row)
        raise ValueError("该运行中不存在此考生")

    def candidates(self, run_id, filters=None, page=1, limit=50):
        f = filters or {}
        records = [candidate_record(r) for r in self._runs[run_id]["candidates"]]
        mapping = {"school": "senior_code", "junior": "junior_code", "batch": "admitted_batch", "plan": "plan_id"}
        records = [r for r in records if all(str(r[field]) == str(f[k]) for k, field in mapping.items() if f.get(k))
                   and (not f.get("q") or f["q"] in r["candidate_no"] or f["q"] in r["name"])
                   and (f.get("status") not in ("admitted", "unadmitted") or
                        (r["admitted_batch"] is not None) == (f["status"] == "admitted"))]
        records.sort(key=lambda r: (r["plan_id"] or "", r["admission_order"] or 0, r["candidate_no"]))
        return records[(page - 1) * limit:page * limit], len(records)

    def plans(self, run_id, filters=None, page=1, limit=50):
        f = filters or {}
        rows = [r for r in self._runs[run_id]["plans"] if
                all(str(r[k]) == str(f[k]) for k in ("school", "junior", "batch") if f.get(k))]
        return copy.deepcopy(rows[(page - 1) * limit:page * limit]), len(rows)

    def transfers(self, run_id):
        return [dict(transfer_no=i, source_plan_id=t[0], target_plan_id=t[1], seats=t[2])
                for i, t in enumerate(self._runs[run_id]["state"]["transfers"], 1)]

    def overview(self, run_id):
        candidates, _ = self.candidates(run_id, limit=10**9)
        plans, _ = self.plans(run_id, limit=10**9)
        batches, scores, juniors, schools = {}, {}, {}, {}
        for c in candidates:
            b = c["admitted_batch"]
            batches[b] = batches.get(b, 0) + 1
            for groups, key, field in [(scores, c["score"] // 50 * 50, "lower_score"), (juniors, c["junior_code"], "junior_code")]:
                row = groups.setdefault(key, {field: key, "candidate_count": 0, "admitted_count": 0})
                row["candidate_count"] += 1
                row["admitted_count"] += b is not None
        for p in plans:
            row = schools.setdefault(p["school"], {"school": p["school"], "initial_seats": 0, "admitted_count": 0, "remaining_seats": 0})
            for k in ("initial_seats", "admitted_count", "remaining_seats"):
                row[k] += p[k]
        return {"batches": [{"batch": b, "count": n} for b, n in sorted(batches.items(), key=lambda x: x[0] or 0)],
                "scores": [scores[k] for k in sorted(scores)], "juniors": [juniors[k] for k in sorted(juniors)],
                "schools": [schools[k] for k in sorted(schools)]}

    def iter_export(self, run_id, kind, filters, expected_batch):
        with self.lock:
            if self.run(run_id)["completed_batch"] != expected_batch:
                raise Conflict("运行已推进，请重新导出")
            rows = self.transfers(run_id) if kind == "transfers" else getattr(self, kind)(run_id, filters, limit=10**9)[0]
        yield from rows

    def audit(self, run_id):
        data, rules, state = self.load(run_id)
        verify_state(data, rules, state)
        rows, plans = projection(data, rules, state)
        row = self._runs[run_id]
        if (row["candidates"] != rows or row["plans"] != plans or
                row["admitted_count"] != sum(p["admitted_count"] for p in plans) or
                row["remaining_seats"] != sum(state["balances"].values())):
            raise ValueError("内存投影与检查点不一致")
        return {"status": "verified", "candidates": len(rows), "plans": len(plans),
                "transfers": len(state["transfers"]), "completed_batch": state["completed_batch"]}
