# 简化广州中考公办高中录取模拟系统

《软件系统开发实践》课程设计，使用虚构数据进行教学模拟。目标是完成数据生成与管理、MySQL 导入、四批投档、结果保存、查询统计，以及约 15 万考生规模的真实实验和优化对照。

## 当前进度

更新日期：2026 年 11 月 18 日。

- 已完成四批投档、第二批初中指标隔离、第一和第二批余额转入第三批，以及名额收支校验。
- 已完成 20 人样例、两组高分滑档对照和 13 项自动化测试。
- 已完成 1 万、5 万、15 万人数据生成及纯 Python 性能基准。
- 已向独立 MySQL 实验库导入 15 万考生和 1149399 条志愿，并核对全部输入内容。
- 已从 MySQL 读取 15 万人输入，执行四批投档，完整结果与纯 Python 基准一致。
- 已事务保存完整输入快照、全部处理日志、115291 条录取明细、48250 条余额和 8378 条结转流水；提交后用新连接完整查回核对。
- 已完成 6 类考生的查询基准：预跑 1 轮、正式计时 3 轮，共 24 次查询结果核对通过。

当前主要通过命令行运行。Flask 页面、完整数据管理、规则参数化、按批执行与进度展示、统计导出，以及冗余优化和前后对照仍待完成。计划统计已有样例查询实现，15 万人规模的计划统计性能基准尚未完成。

正式的 15 万人已保存运行编号：

~~~text
0113c1d3-e855-42db-a16f-2578e5f3ab78
~~~

对应记录见 [保存报告](experiments/mysql_save_150000_v2/save_report.json) 和 [考生查询基准](experiments/mysql_candidate_query_150000_v1/candidate_query_benchmark.json)。这次保存和查询验收不代表最终系统及优化工作已经全部完成。

## 技术与数据库

| 项目 | 当前使用情况 |
|---|---|
| Python | CPython 3.14.3 |
| MySQL | Community Server 8.4.13 |
| 数据库驱动 | mysql-connector-python 26.10.0 |
| 页面方案 | Flask + Jinja，页面待实现 |
| 开发与命令行 | PyCharm、PowerShell |

| 数据库 | 用途 |
|---|---|
| admissionsim | 原 20 人样例、保存及查询验收 |
| admissionsim_scale_150000 | 独立 15 万人数据及规模实验 |

save_admission_run.py、query_candidate.py、report_admission_run.py 的命令行入口默认连接原样例库。规模脚本固定连接独立实验库。数据库脚本通过 getpass 在运行时读取应用账号密码。

当前规则参数固定为全市控制线 600、梯度线 720/680/640/600，志愿上限为 1/3/6/6；运行参数化仍是后续工作。所有学校、考生与成绩均为虚构数据。

## 文件索引

当前保留了主流程、测试、实验和诊断脚本。后续会统一公共逻辑并整理目录，整理时同步维护导入路径、运行命令和实验复现说明。

### 投档与结果查询

| 文件 | 作用 |
|---|---|
| [admission.py](admission.py) | 第一批自主招生、资格与排名录取，以及余额结转；含早期独立演示数据 |
| [second_batch.py](second_batch.py) | 第二批梯度投档、志愿轮次、初中指标隔离及余额结转 |
| [third_batch.py](third_batch.py) | 完整 20 人样例，以及第三、第四批共用的统招投档函数 |
| [run_admissions.py](run_admissions.py) | 四批统一入口、输入校验、名额平衡检查和 MySQL 输入读取 |
| [save_admission_run.py](save_admission_run.py) | 原样例库的运行快照、日志及结果事务保存 |
| [save_scale_run.py](save_scale_run.py) | 独立实验库的 15 万人结果保存、发送大小检查、提交状态记录及完整查回核对 |
| [query_candidate.py](query_candidate.py) | 原样例库的考生去向与处理过程查询；内部函数也被规模查询基准复用 |
| [report_admission_run.py](report_admission_run.py) | 原样例库的运行汇总、逐计划录取数、余额、最低分及末位考生统计 |

### 数据与数据库脚本

| 文件 | 作用 |
|---|---|
| [generate_scale_data.py](generate_scale_data.py) | 固定种子生成独立规模数据、清单和摘要，支持已有数据校验 |
| [import_scale_data.py](import_scale_data.py) | 向独立实验库分批导入 15 万人输入，逐行核对并生成导入报告；支持只读检查 |
| [run_scale_admissions.py](run_scale_admissions.py) | 从实验库读取输入、执行四批投档、对照纯 Python 基准并保存本地结果；不写数据库结果表 |
| [schema.sql](schema.sql) | 原样例库六张基础输入表的结构 |
| [schema_runs.sql](schema_runs.sql) | 原样例库四张运行结果表的结构 |
| [schema_scale.sql](schema_scale.sql) | 独立实验库的输入表、数据集清单、结果表结构及应用账号授权 |
| [seed_sample.sql](seed_sample.sql) | 原样例库的完整 20 人测试数据 |

### 测试与性能基准

| 文件 | 作用 |
|---|---|
| [test_admissions.py](test_admissions.py) | 录取规则、边界、名额隔离、结转、滑档与重复运行的回归测试 |
| [test_input_validation.py](test_input_validation.py) | 重复考生、非法分数、重复志愿等异常输入测试 |
| [compare_choices.py](compare_choices.py) | 从原始样例独立执行两组志愿调整，对照滑档结果 |
| [db_check.py](db_check.py) | 原样例库的应用账号连接检查工具 |
| [benchmark_admissions.py](benchmark_admissions.py) | 纯 Python 规模基准，记录预跑、独立进程计时、单独内存测量和结果摘要 |
| [benchmark_scale_candidate_queries.py](benchmark_scale_candidate_queries.py) | 固定 6 类考生的查询基准，记录原始耗时、结果核对和中位数 |
| [小数据集预测试与验收.txt](小数据集预测试与验收.txt) | 20 人固定输入、人工推演、滑档案例和验收清单 |

### 阶段性诊断工具

以下三个文件用于排查大结果写入断线，保留为实验过程证据，后续归档到诊断工具目录，不作为主系统操作入口。

| 文件 | 作用 |
|---|---|
| [probe_scale_payload.py](probe_scale_payload.py) | 只读发送完整快照和日志文本，检查服务器返回的长度与 SHA256 |
| [probe_scale_json.py](probe_scale_json.py) | 只读检查 MySQL JSON 转换、内容计数和二进制表示大小 |
| [probe_scale_write_rollback.py](probe_scale_write_rollback.py) | 用新 UUID 插入运行主记录，随后回滚，并用新连接确认无残留记录；不提交录取结果 |

### 仓库说明与实验记录

| 文件 | 作用 |
|---|---|
| [README.md](README.md) | 项目状态、文件用途、运行方式、实验口径和后续工作 |
| [.gitignore](.gitignore) | 排除密码、本机环境、缓存、教师原始材料和可重新生成的大文件 |

experiments 目录保存数据清单和实验报告：

| 目录或文件 | 作用 |
|---|---|
| scale_10000_a、scale_10000_b 中的 manifest.json | 1 万人数据及同配置复现清单 |
| scale_50000、scale_150000 中的 manifest.json | 5 万、15 万人数据集标识、生成配置、计数及摘要 |
| baseline_10000_v1、baseline_50000_v1、baseline_150000_v1 中的 benchmark.json | 三种规模纯 Python 基准的原始样本、汇总、环境及结果摘要 |
| [mysql_import_150000_v1/import_report.json](experiments/mysql_import_150000_v1/import_report.json) | 真实 MySQL 导入、提交和核对记录 |
| [mysql_run_150000_v1/run_report.json](experiments/mysql_run_150000_v1/run_report.json) | 从 MySQL 读取并执行四批投档、与纯 Python 结果完整对照的记录 |
| [mysql_save_150000_v1/save_report.json](experiments/mysql_save_150000_v1/save_report.json) | 首次结果保存失败记录，状态为 not_committed，保留失败证据 |
| [mysql_payload_probe_150000_v1/probe_report.json](experiments/mysql_payload_probe_150000_v1/probe_report.json) | 大文本传输诊断记录 |
| [mysql_json_probe_150000_v1/json_probe_report.json](experiments/mysql_json_probe_150000_v1/json_probe_report.json) | JSON 转换和内容计数诊断记录 |
| [mysql_write_probe_150000_v1/write_probe_report.json](experiments/mysql_write_probe_150000_v1/write_probe_report.json) | 主记录写入、回滚及新连接查无记录的诊断记录 |
| [mysql_save_150000_v2/save_report.json](experiments/mysql_save_150000_v2/save_report.json) | 正式结果保存及提交后完整查回记录，状态为 verified |
| [mysql_candidate_query_150000_v1/candidate_query_benchmark.json](experiments/mysql_candidate_query_150000_v1/candidate_query_benchmark.json) | 6 类考生共 24 次查询的预跑、正式耗时、结果和中位数 |

大规模 data.json 和 baseline_result.json 通过脚本生成，已被 Git 忽略；清单和小体积报告纳入版本管理。教师原始材料、个人交接说明、密码、虚拟环境和 IDE 配置只保留本地。

## 已完成的规模实验

### 纯 Python 基准

默认随机种子为 20261118，500 所初中、150 所高中。预跑 1 次，正式计时 3 次取中位数；每轮为独立新进程，另用一轮 tracemalloc 测量内存。

| 考生数 | 投档秒数 | 本地流水线秒数 | Python 追踪峰值 MiB | 录取人数 |
|---:|---:|---:|---:|---:|
| 10000 | 0.093290 | 0.187509 | 27.36 | 7689 |
| 50000 | 0.675975 | 1.187511 | 135.48 | 38509 |
| 150000 | 2.345074 | 3.925678 | 410.57 | 115291 |

本地流水线包含 JSON 读取、校验、投档和序列化，不含 MySQL、进程启动、模块导入及结果文件写入。未清空操作系统缓存；追踪峰值是 Python 分配峰值，不等于进程 RSS。

### 15 万人 MySQL 实验

15 万人数据共有 48250 条计划、1149399 条志愿、15807 条资格和 120000 个初始名额。四批分别录取 6184、39722、50094、19291 人，总录取 115291 人，未录取 34709 人，剩余名额 4709。

| 阶段 | 实测秒数 | 口径 |
|---|---:|---|
| 输入写入与提交 | 106.288627 | 清单、六张输入表写入及提交；不含源读取、逐行核对和提交后检查 |
| 从 MySQL 读取投档输入 | 3.109095 | SQL、传输、驱动转换和 Python 输入结构构建 |
| 四批投档 | 2.645071 | 内存执行，包含内部输入校验 |
| 结果 SQL 写入合计 | 29.559900 | 运行主记录、余额、录取明细和结转流水 |
| 结果事务提交 | 0.379627 | 一次事务提交 |
| 提交后完整查回核对 | 3.114886 | 新连接、完整快照与日志、明细顺序、余额及结转核对 |

以上来自不同阶段报告，各为单次测量，不能相加作为一次连续流程的端到端耗时。未清空 MySQL 和操作系统缓存；正式结果保存前的诊断也会预热缓存。结果 SQL 写入与提交合计为 29.939527 秒，保存脚本使用纯 Python 驱动模式。

首次保存曾出现连接断开，根因尚未确认；失败与诊断记录均保留。成功保存不能证明旧故障已彻底解决。提交响应丢失时应先按报告中的 run_id 查回，不自动重复保存。

### 考生查询基准

固定 6 个样本，每个预跑 1 次、正式计时 3 次；复用一个连接和只读一致性快照，每次重新读取整次快照与日志。

| 考生号 | 样本类别 | 查询总耗时中位数 秒 |
|---|---|---:|
| S000002 | 第一批录取 | 1.676706 |
| S050460 | 第二批录取 | 1.744444 |
| S099797 | 第三批录取 | 1.728369 |
| S149993 | 第四批录取 | 1.821884 |
| S073470 | 达线未录取 | 1.734112 |
| S074889 | 低于控制线未录取 | 1.714629 |

总耗时包含读取与解析、构建处理过程及释放整次查询上下文；排除连接、本地准备、预检、显式 GC、结果核对、输出和报告写入。各阶段中位数不能相加代替总耗时中位数。本步没有测量内存，也没有实施查询优化。

## 已有本地环境中的常用命令

在项目根目录的 PowerShell 中执行，使用项目虚拟环境解释器。以下命令面向已初始化的本地环境，完整的新机器部署说明仍待补齐。

回归测试：

~~~powershell
.\.venv\Scripts\python.exe -B -m unittest -v test_admissions test_input_validation
~~~

运行内置 20 人样例：

~~~powershell
.\.venv\Scripts\python.exe -B .\run_admissions.py
~~~

只读核对已导入的 15 万人输入：

~~~powershell
.\.venv\Scripts\python.exe -B .\import_scale_data.py --dataset .\experiments\scale_150000 --check
~~~

只读查回正式保存的 15 万人结果：

~~~powershell
.\.venv\Scripts\python.exe -B .\save_scale_run.py --out .\experiments\mysql_save_150000_v2 --check
~~~

重新做实验时必须指定新的输出目录；写入实验使用新的运行 UUID。已有输入库、基准结果及实验目录不覆盖。

## 后续交付事项

- 补齐 15 万人计划统计查询基准，保留必要的优化前参照。
- 整理目录与统一公共服务，完善规则参数化、按批执行、运行状态和进度展示。
- 实现有明确范围的冗余状态维护和查询优化，验证事务一致性，并用相同数据、追踪口径、缓存条件和重复次数做前后对照。
- 完善基础数据管理、多维查询、分数分布、末位志愿与同分序号等统计及导出。
- 完成 Flask + Jinja 页面，串联完整演示流程。
- 补齐配置示例、依赖、MySQL 初始化及使用部署说明。
- 完成设计报告、ER 图和必要流程图、系统测试材料、演示脚本与答辩准备。

## AI 辅助说明

使用 AI 辅助讨论规则、生成部分代码、设计测试及核对本地实验记录。真实 MySQL 实验由本人在本机运行并验收，实际测量保存在实验报告中。内存或模拟连接验证与真实 MySQL 实验分别说明，不用模拟耗时替代真实测量。
