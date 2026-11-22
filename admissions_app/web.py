import csv
import io
import json
import secrets
from dataclasses import asdict

from flask import Flask, Response, abort, flash, jsonify, redirect, render_template, request, session, url_for

from .domain import Rules, edit_entity, normalize, sample_dataset
from .repository import CANDIDATE_FIELDS, PLAN_FIELDS
from .service import ROOT, Service, provenance


def create_app(repository, *, testing=False):
    app = Flask(__name__, template_folder=str(ROOT / "admissions_app/templates"), static_folder=str(ROOT / "admissions_app/static"))
    app.config.update(SECRET_KEY=secrets.token_hex(32), MAX_CONTENT_LENGTH=64 * 1024 * 1024,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict", TESTING=testing)
    service = Service(repository)
    app.extensions.update(repository=repository, admission_service=service)

    @app.get("/health")
    def health():
        response = jsonify(application="admissionsim-course-design",
                           storage="memory" if repository.mode.startswith("内存") else "mysql")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.before_request
    def csrf():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(32)
        if request.method == "POST" and not secrets.compare_digest(session["csrf"], request.form.get("csrf", "")):
            abort(400, description="页面校验已过期，请刷新后重试")

    @app.context_processor
    def context():
        return {"storage_mode": repository.mode, "csrf_token": session.get("csrf"), "default_rules": asdict(Rules())}

    @app.template_filter("shown")
    def shown(value):
        return "无" if value is None else value

    @app.errorhandler(ValueError)
    def invalid(exc):
        return render_template("error.html", message=str(exc)), 400

    @app.errorhandler(413)
    def large(exc):
        return render_template("error.html", message="文件超过 64 MiB，请通过命令行导入"), 413

    @app.errorhandler(500)
    def internal(exc):
        return render_template("error.html", message="操作未完成。请检查终端；若发生保存错误，请先刷新运行并核对已提交检查点。"), 500

    def rules_from_form():
        return Rules(**{k: int(request.form.get(k, v)) for k, v in asdict(Rules()).items()})

    def page_no():
        try:
            return max(1, int(request.args.get("page", 1)))
        except ValueError:
            raise ValueError("页码必须为整数") from None

    @app.get("/")
    def home():
        return render_template("home.html", datasets=repository.datasets(), runs=repository.runs())

    @app.post("/datasets/sample")
    def sample():
        data = sample_dataset()
        variant = request.form.get("variant", "original")
        if variant in ("A", "B"):
            cid = "S11" if variant == "A" else "S12"
            next(c for c in data["candidates"] if c["id"] == cid)["choices"][3] = ["B", "A"]
        elif variant != "original":
            raise ValueError("未知样例")
        identifier = repository.put_dataset(data, "20 人样例" + (f" · 滑档对照 {variant}" if variant != "original" else " · 原始输入"))
        return redirect(url_for("dataset", dataset_id=identifier))

    @app.post("/datasets/import")
    def import_data():
        upload = request.files.get("file")
        if not upload:
            raise ValueError("请选择 data.json 文件")
        try:
            data = normalize(json.load(upload.stream))
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"JSON 输入不合法：{exc}") from None
        identifier = repository.put_dataset(data, request.form.get("label", "导入数据"))
        return redirect(url_for("dataset", dataset_id=identifier))

    @app.post("/datasets/generate")
    def generate():
        from admissions_app.datasets import build_dataset
        count, seed = int(request.form["count"]), int(request.form["seed"])
        if not 20 <= count <= 150000:
            raise ValueError("生成规模须在 20 至 150000 之间")
        config = {"count": count, "seed": seed, "juniors": min(500, max(2, count // 100)),
                  "third_schools": min(100, max(2, count // 1000)), "fourth_schools": min(50, max(1, count // 2000)),
                  "seat_percent": 80, "third_seat_percent": 80, "score_mean": 660, "score_sd": 70,
                  "first_percent": 15, "qualified_percent": 70, "hot_percent": 60,
                  "city_cutoff": 600, "gradient_lines": [720, 680, 640, 600], "choice_limits": {str(k): v for k, v in {1: 1, 2: 3, 3: 6, 4: 6}.items()}}
        identifier = repository.put_dataset(build_dataset(config), f"生成 {count} 人 · 种子 {seed}")
        return redirect(url_for("dataset", dataset_id=identifier))

    @app.route("/datasets/<dataset_id>", methods=["GET", "POST"])
    def dataset(dataset_id):
        data = repository.dataset(dataset_id)
        kind = request.values.get("kind", "candidates")
        if kind not in {"candidates", "plans", "junior_schools", "senior_schools", "autonomous_ranks"}:
            raise ValueError("未知数据类别")
        key = request.values.get("key", "")
        if request.method == "POST":
            value = None
            if request.form.get("operation") != "delete":
                if kind == "candidates" and request.form.get("editor") == "candidate":
                    value = {"id": key, "name": request.form["name"], "junior": request.form["junior"],
                             "score": int(request.form["score"]), "tie": int(request.form["tie"]),
                             "choice": request.form["choice"].strip(), "second_choices": request.form["second_choices"].split(),
                             "choices": {3: request.form["third_choices"].split(), 4: request.form["fourth_choices"].split()}}
                else:
                    value = json.loads(request.form["value"])
            edited = edit_entity(data, kind, key, value)
            identifier = repository.put_dataset(edited, request.form.get("label", "编辑版本"), parent_id=dataset_id)
            flash("已创建新版本；已有运行仍使用原输入")
            return redirect(url_for("dataset", dataset_id=identifier, kind=kind, key=key))
        all_rows = [(c["id"], c) for c in data[kind]] if kind == "candidates" else list(data[kind].items())
        selected = next((v for k, v in all_rows if k == key), {})
        query = request.args.get("q", "")
        rows = [(k, v) for k, v in all_rows if not query or query in k]
        page = page_no()
        return render_template("dataset.html", dataset_id=dataset_id, data=data, kind=kind, key=key,
                               rows=rows[(page - 1) * 30:page * 30], total=len(rows), page=page,
                               selected=json.dumps(selected, ensure_ascii=False, indent=2), selected_object=selected)

    @app.get("/datasets/<dataset_id>/export")
    def dataset_export(dataset_id):
        return Response(json.dumps(repository.dataset(dataset_id), ensure_ascii=False, indent=2),
                        mimetype="application/json", headers={"Content-Disposition": f'attachment; filename="{dataset_id}.json"'})

    @app.post("/runs")
    def create_run():
        identifier = repository.create_run(request.form["dataset_id"], rules_from_form(), provenance())
        return redirect(url_for("run", run_id=identifier))

    @app.get("/runs/<run_id>")
    def run(run_id):
        meta = repository.run(run_id)
        filters = {k: request.args.get(k, "") for k in ("school", "junior", "batch", "plan", "status", "q")}
        view = request.args.get("view", "candidates")
        if view not in ("candidates", "plans"):
            raise ValueError("未知结果视图")
        page = page_no()
        reader = repository.plans if view == "plans" else repository.candidates
        rows, total = reader(run_id, filters, page)
        overview = repository.overview(run_id)
        status = service.status(run_id)
        if status["completed_batch"] != meta["completed_batch"]:
            return redirect(request.url)
        return render_template("run.html", meta=meta, filters=filters, view=view, rows=rows, total=total,
                               page=page, status=status, overview=overview)

    @app.post("/runs/<run_id>/execute")
    def execute(run_id):
        service.start(run_id, full=request.form.get("mode") == "full")
        return redirect(url_for("run", run_id=run_id))

    @app.get("/runs/<run_id>/status")
    def status(run_id):
        return jsonify(service.status(run_id))

    @app.post("/runs/<run_id>/audit")
    def audit(run_id):
        result = repository.audit(run_id)
        flash(f"完整一致性核对通过：{result['candidates']} 名考生、{result['plans']} 条计划、{result['transfers']} 条结转")
        return redirect(url_for("run", run_id=run_id))

    @app.get("/runs/<run_id>/candidates/<candidate_no>")
    def candidate(run_id, candidate_no):
        meta = repository.run(run_id)
        return render_template("candidate.html", meta=meta, row=repository.candidate(run_id, candidate_no))

    @app.get("/runs/<run_id>/transfers")
    def transfers(run_id):
        return render_template("transfers.html", meta=repository.run(run_id), rows=repository.transfers(run_id))

    @app.get("/runs/<run_id>/statistics.csv")
    def statistics_export(run_id):
        meta = repository.run(run_id)
        overview = repository.overview(run_id)
        if repository.run(run_id)["completed_batch"] != meta["completed_batch"]:
            raise ValueError("统计导出期间批次已推进，请重试")
        fields = ["run_id", "completed_batch", "dimension", "key", "candidate_count", "initial_seats", "admitted_count", "remaining_seats"]
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=fields)
        writer.writeheader()
        def add(dimension, key, **values):
            writer.writerow({"run_id": run_id, "completed_batch": meta["completed_batch"],
                             "dimension": dimension, "key": key, **values})
        add("overall", "all", candidate_count=meta["candidate_count"], admitted_count=meta["admitted_count"],
            initial_seats=meta["admitted_count"] + meta["remaining_seats"], remaining_seats=meta["remaining_seats"])
        for row in overview["batches"]:
            add("batch", row["batch"] if row["batch"] is not None else "not_admitted",
                candidate_count=row["count"], admitted_count=row["count"] if row["batch"] else 0)
        for row in overview["scores"]:
            add("score", f"{row['lower_score']}-{row['lower_score'] + 49}",
                candidate_count=row["candidate_count"], admitted_count=row["admitted_count"])
        for row in overview["juniors"]:
            add("junior", row["junior_code"], candidate_count=row["candidate_count"], admitted_count=row["admitted_count"])
        for row in overview["schools"]:
            add("senior", row["school"], initial_seats=row["initial_seats"], admitted_count=row["admitted_count"],
                remaining_seats=row["remaining_seats"])
        return Response("\ufeff" + buffer.getvalue(), mimetype="text/csv", headers={
            "Content-Disposition": f'attachment; filename="statistics-{run_id}.csv"'})

    @app.get("/runs/<run_id>/export/<kind>")
    def export(run_id, kind):
        meta = repository.run(run_id)
        if kind not in ("candidates", "plans", "transfers"):
            abort(404)
        filters = {k: request.args.get(k, "") for k in ("school", "junior", "batch", "plan", "status", "q")}
        fields = list(PLAN_FIELDS if kind == "plans" else CANDIDATE_FIELDS)
        if kind == "transfers":
            fields = ["transfer_no", "source_plan_id", "target_plan_id", "seats"]
        def safe(v):
            value = "" if v is None else str(v)
            return "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value
        def stream():
            yield "\ufeff"
            buffer = io.StringIO(newline="")
            writer = csv.writer(buffer)
            writer.writerow(["run_id", "completed_batch", *fields])
            yield buffer.getvalue()
            records = repository.iter_export(run_id, kind, filters, meta["completed_batch"])
            for row in records:
                buffer.seek(0)
                buffer.truncate(0)
                writer.writerow([run_id, meta["completed_batch"], *(safe(row[k]) for k in fields)])
                yield buffer.getvalue()
        return Response(stream(), mimetype="text/csv", headers={"Content-Disposition": f'attachment; filename="{kind}-{run_id}.csv"'})

    return app
