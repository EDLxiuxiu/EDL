"""唯一主系统命令入口。密码只在本机 getpass 提示中输入。"""
import argparse
import json
from getpass import getpass
from pathlib import Path

from admissions_app.domain import Rules, normalize, sample_dataset
from admissions_app.repository import MemoryRepository, MySQLRepository, connection_settings
from admissions_app.service import ROOT, Service, provenance


def initialize_database(password):
    import mysql.connector
    settings = connection_settings()
    with mysql.connector.connect(host=settings["host"], port=settings["port"], user="root", password=password,
                                 charset="utf8mb4", use_pure=True) as connection:
        with connection.cursor() as cursor:
            sql = (ROOT / "schema_app.sql").read_text(encoding="utf-8")
            sql = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
            for statement in sql.split(";"):
                if statement.strip():
                    cursor.execute(statement)
            # 为已存在的 app-v1 主库补充有实测依据的覆盖索引；不改历史库。
            for index, columns in (("ix_candidate_batch", "run_id,admitted_batch,candidate_no"),
                                   ("ix_candidate_statistics", "run_id,junior_code,score,admitted_batch")):
                cursor.execute("SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema='admissionsim_app' "
                               "AND table_name='app_candidate_results' AND index_name=%s", (index,))
                if cursor.fetchone()[0] == 0:
                    cursor.execute(f"ALTER TABLE admissionsim_app.app_candidate_results ADD INDEX {index} ({columns})")
        connection.commit()


def main():
    parser = argparse.ArgumentParser(description="中考投档模拟系统统一入口")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="用既有 root 账号初始化独立主库及授权")
    web = sub.add_parser("web", help="启动本机 Flask 界面")
    web.add_argument("--demo", action="store_true", help="内存演示，不连接 MySQL，重启清空")
    web.add_argument("--port", type=int, default=5000)
    imp = sub.add_parser("import", help="导入新数据版本；不覆盖旧版本")
    imp.add_argument("path", nargs="?", type=Path, help="data.json；省略则使用 20 人样例")
    imp.add_argument("--label", default="导入数据")
    run = sub.add_parser("run", help="新建运行并执行，或恢复既有运行")
    run.add_argument("--dataset-id")
    run.add_argument("--resume", help="既有主系统 run_id，从已提交批次继续")
    run.add_argument("--one-batch", action="store_true")
    for key, value in vars(Rules()).items():
        run.add_argument("--" + key.replace("_", "-"), type=int, default=value)
    check = sub.add_parser("check", help="只读核对主系统检查点与全部冗余")
    check.add_argument("run_id")
    bench = sub.add_parser("benchmark", help="真实 MySQL 查询基准及优化对照")
    bench.add_argument("--out", type=Path, required=True)
    bench.add_argument("--run-id", help="主系统中同一 15 万人数据的已完成运行；省略则只补旧全运行计划基准")
    bench.add_argument("--repeat", type=int, default=3)
    accept = sub.add_parser("accept", help="在独立主库完成样例、15 万人保存、全量核对和查询对照")
    accept.add_argument("--out", type=Path, required=True)
    accept.add_argument("--resume", action="store_true", help="只恢复同一目录和固定 UUID；不覆盖已完成阶段")
    local = sub.add_parser("verify-local", help="自动化测试与新实现 15 万人本地正确性验收，不连接数据库")
    local.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "verify-local":
        from admissions_app.experiments import verify_local
        verify_local(args.out)
        return
    if args.command == "init-db":
        initialize_database(getpass("请输入现有 root 密码（不保存）："))
        print("独立主库 admissionsim_app 初始化完成；账号密码没有改变")
        return
    if args.command == "web" and args.demo:
        repository = MemoryRepository()
        repository.put_dataset(sample_dataset(), "20 人原始样例")
    else:
        password = getpass("请输入现有 admission_app 应用密码（仅保留在当前进程内存）：")
        if args.command in ("benchmark", "accept"):
            from admissions_app.experiments import acceptance, benchmark
            if args.command == "benchmark":
                benchmark(password, args.out, run_id=args.run_id, repeat=args.repeat)
            else:
                acceptance(password, args.out, resume=args.resume)
            return
        repository = MySQLRepository(password)
    if args.command == "web":
        from admissions_app.web import create_app
        print("存储模式：", repository.mode)
        create_app(repository).run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False, threaded=True)
    elif args.command == "import":
        data = normalize(json.loads(args.path.read_text(encoding="utf-8"))) if args.path else sample_dataset()
        print("数据版本：", repository.put_dataset(data, args.label))
    elif args.command == "run":
        if bool(args.resume) == bool(args.dataset_id):
            parser.error("--dataset-id 与 --resume 必须且只能提供一个")
        rules = Rules(**{k: getattr(args, k) for k in vars(Rules())})
        run_id = args.resume or repository.create_run(args.dataset_id, rules, provenance())
        print("运行编号：", run_id, flush=True)
        Service(repository).execute(run_id, full=not args.one_batch, progress=lambda s: print(s, flush=True))
        print(json.dumps(repository.audit(run_id), ensure_ascii=False))
    elif args.command == "check":
        print(json.dumps(repository.audit(args.run_id), ensure_ascii=False))


if __name__ == "__main__":
    main()
