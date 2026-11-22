"""交付文档构建工具；使用 Codex bundled Python 的 python-docx 与 Pillow。"""
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
ASSETS = DOCS / "assets"
FONT_PATH = "C:/Windows/Fonts/msyh.ttc"


def diagram(name, title, boxes, arrows, height=750):
    image = Image.new("RGB", (1440, height), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(FONT_PATH, 25)
    small = ImageFont.truetype(FONT_PATH, 22)
    heading = ImageFont.truetype(FONT_PATH, 30)
    draw.text((35, 20), title, fill="#172b40", font=heading)
    for points, label, label_xy in arrows:
        draw.line(points, fill="#668294", width=3)
        x, y = points[-1]
        px, py = points[-2]
        if x == px:
            sign = 1 if y > py else -1
            draw.polygon([(x, y), (x - 7, y - sign * 13), (x + 7, y - sign * 13)], fill="#668294")
        else:
            sign = 1 if x > px else -1
            draw.polygon([(x, y), (x - sign * 13, y - 7), (x - sign * 13, y + 7)], fill="#668294")
        draw.text(label_xy, label, font=small, fill="#405d70")
    for x, y, w, h, label, lines in boxes:
        draw.rounded_rectangle((x, y, x + w, y + h), radius=9, fill="#f2f7f8", outline="#a5bdc9", width=2)
        draw.text((x + 18, y + 16), label, font=font, fill="#0b6661")
        draw.line((x + 16, y + 60, x + w - 16, y + 60), fill="#d1dee4", width=2)
        for i, line in enumerate(lines):
            draw.text((x + 18, y + 77 + i * 34), line, font=small, fill="#263e50")
    image.save(ASSETS / f"{name}.png")


def diagrams():
    ASSETS.mkdir(parents=True, exist_ok=True)
    diagram("architecture", "主系统架构与复用关系", [
        (40, 100, 375, 180, "操作入口", ["Flask + Jinja 页面", "manage.py 统一命令"]),
        (540, 100, 375, 180, "公共应用服务", ["Service 执行与进度", "统一校验与批次推进"]),
        (1040, 100, 360, 180, "领域计算", ["Rules / advance / projection", "复用现有四批投档函数"]),
        (540, 410, 375, 180, "存储接口", ["版本、事务、检查点、查询", "MySQL / 明确标注的内存演示"]),
        (1040, 410, 360, 180, "MySQL 独立主库", ["admissionsim_app", "输入版本 + 结构化结果"]),
    ], [([(415, 190), (540, 190)], "调用", (443, 150)),
        ([(915, 190), (1040, 190)], "计算", (943, 150)),
        ([(725, 280), (725, 410)], "保存与读取", (740, 325)),
        ([(915, 500), (1040, 500)], "事务", (943, 460))], 650)
    diagram("er", "主系统物理数据关系", [
        (40, 100, 390, 190, "app_datasets", ["PK dataset_id / FK parent_id", "完整不可变输入聚合", "content_sha / payload"]),
        (540, 100, 390, 190, "app_runs", ["PK run_id / FK dataset_id", "rules / provenance", "completed_batch / 汇总"]),
        (1040, 100, 360, 190, "app_checkpoints", ["PK run_id + batch_no", "FK run_id", "state_sha / 状态与日志"]),
        (40, 430, 390, 220, "app_plan_results", ["PK run_id + plan_id", "初始、转入、转出、录取、余额", "最低分 / 实际末位信息", "FK run_id"]),
        (540, 430, 390, 220, "app_candidate_results", ["PK run_id + candidate_no", "FK run_id / 复合计划外键", "录取状态 / 过程 / 余额事件", "UNIQUE 计划内实际顺序"]),
        (1040, 430, 360, 220, "app_transfers", ["PK run_id + transfer_no", "FK 来源与目标计划", "UNIQUE run_id + 来源", "seats > 0"]),
    ], [([(430, 190), (540, 190)], "1 : N", (452, 151)),
        ([(930, 190), (1040, 190)], "1 : N", (952, 151)),
        ([(735, 290), (735, 430)], "1 : N", (750, 338)),
        ([(570, 290), (570, 350), (235, 350), (235, 430)], "1 : N", (340, 315)),
        ([(430, 530), (540, 530)], "1 : N", (450, 490)),
        ([(235, 650), (235, 720), (1220, 720), (1220, 650)], "1 : N  来源与目标两条关系", (570, 679))], 780)
    diagram("flow", "分批执行与检查点事务", [
        (25, 150, 245, 190, "固定输入与规则", ["校验完整数据", "创建第 0 批检查点", "记录版本与源码摘要"]),
        (310, 150, 245, 190, "读取最近检查点", ["恢复已完成批次", "完整执行复用排序", "确定下一批"]),
        (595, 150, 245, 190, "内存投档", ["梯度与志愿轮次", "更新录取与余额", "结转并检查约束"]),
        (880, 150, 245, 190, "生成投影", ["按考生索引日志", "逐计划统计", "保留实际顺序"]),
        (1165, 150, 245, 190, "一次事务提交", ["条件更新预期批次", "投影 + 流水", "+ 新检查点"]),
        (450, 480, 520, 140, "失败或进程中断", ["不修改已提交检查点，查回后继续下一批"]),
    ], [([(270, 240), (310, 240)], "", (0, 0)), ([(555, 240), (595, 240)], "", (0, 0)),
        ([(840, 240), (880, 240)], "", (0, 0)), ([(1125, 240), (1165, 240)], "", (0, 0)),
        ([(1285, 150), (1285, 90), (430, 90), (430, 150)], "完整执行时循环，最多四批", (650, 50)),
        ([(1285, 340), (1285, 550), (970, 550)], "回滚或查回", (1110, 475))], 690)


def set_font(style, size, bold=False):
    style.font.name = "Microsoft YaHei"
    style._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    style.font.size = Pt(size)
    style.font.color.rgb = RGBColor(0, 0, 0)
    style.font.bold = bold


def add_table(doc, lines):
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines]
    rows = [r for r in rows if not all(re.fullmatch(r"[- :]+", cell) for cell in r)]
    table = doc.add_table(rows=0, cols=len(rows[0]))
    table.autofit = False
    ratios = [2, 4.9] if len(rows[0]) == 2 else [2.1, 1.9, 2.9]
    for col, width in zip(table.columns, ratios):
        col.width = Inches(width)
    for ri, values in enumerate(rows):
        cells = table.add_row().cells
        for ci, text in enumerate(values):
            cell = cells[ci]
            cell.width = Inches(ratios[ci])
            cell.text = text.replace("`", "")
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            props = cell._tc.get_or_add_tcPr()
            borders = OxmlElement("w:tcBorders")
            for side in ("top", "left", "bottom", "right"):
                edge = OxmlElement("w:" + side)
                edge.set(qn("w:val"), "single")
                edge.set(qn("w:sz"), "4")
                edge.set(qn("w:color"), "D9D9D9")
                borders.append(edge)
            props.append(borders)
            shade = OxmlElement("w:shd")
            shade.set(qn("w:fill"), "173E57" if ri == 0 else ("F1F5F8" if ri % 2 == 0 else "FFFFFF"))
            props.append(shade)
            margins = OxmlElement("w:tcMar")
            for side in ("top", "left", "bottom", "right"):
                item = OxmlElement("w:" + side)
                item.set(qn("w:w"), "100")
                item.set(qn("w:type"), "dxa")
                margins.append(item)
            props.append(margins)
            for para in cell.paragraphs:
                para.paragraph_format.space_after = Pt(4)
                for run in para.runs:
                    run.font.size = Pt(10)
                    if ri == 0:
                        run.font.bold = True
                        run.font.color.rgb = RGBColor(255, 255, 255)
        trpr = table.rows[-1]._tr.get_or_add_trPr()
        trpr.append(OxmlElement("w:cantSplit"))
        if ri == 0:
            trpr.append(OxmlElement("w:tblHeader"))
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def performance_document():
    path = ROOT / "experiments/app_mysql_query_v3/query_comparison.json"
    if not path.exists():
        return
    import json
    report = json.loads(path.read_text(encoding="utf-8"))
    if report["status"] != "verified":
        return
    groups = {(r["implementation"], r["kind"], r["candidate_no"]): r for r in report["aggregates"]}
    cases = [("all_plans", None, "全部 48250 条计划及全运行汇总")]
    labels = ["第一批录取", "第二批录取", "第三批录取", "第四批录取", "达线未录取", "低于控制线未录取"]
    ids = ["S000002", "S050460", "S099797", "S149993", "S073470", "S074889"]
    cases += [("candidate", cid, f"{cid} {label}") for cid, label in zip(ids, labels)]
    text = ["# 真实 MySQL 查询性能对照", "", "本页由已验收的 query_comparison.json 自动生成，不预填或推算实测值。",
            "", "## 最终重复测量", "", f"主系统运行编号为 {report['app_run_id']}，历史运行编号为 {report['historical_run_id']}。",
            "", "同一 15 万人输入及规则，全部业务结果与文本日志一致。每种实现预跑一轮、正式三轮；每轮检查完整结果，合计 56 次查询核对。",
            "", "| 查询范围 | 旧实现中位数 秒 | 最终实现中位数 秒 | 本次中位数比值 |", "| --- | ---: | ---: | ---: |"]
    for kind, cid, label in cases:
        old, new = groups[("legacy", kind, cid)]["median"], groups[("structured", kind, cid)]["median"]
        text.append(f"| {label} | {old:.6f} | {new:.6f} | {old/new:.2f} |")
    text += ["", "比值为旧实现中位数除以最终实现中位数，只描述本机本次实验。不能推广为所有查询、其他硬件或冷缓存下的收益。",
             "", "每个数据库复用一个纯 Python 驱动连接和只读一致性快照，逐轮交替两种实现先后顺序。未清 MySQL 与操作系统缓存，预检和预跑会预热缓存。总计包含 SQL、传输、驱动转换、JSON 解析、结果构建和大上下文释放；不包含连接、预检、显式 GC、结果核对、摘要和文件写入。不是服务器 SQL 时间，也没有测量内存。",
             "", "## 保留的退化与修正", "", "第一次结构化结果查询已经让考生点查变快，但全运行计划统计反而变慢。第二次补充覆盖索引后，优化器仍为未录名单的排序选择宽主键。最终只对该条已验证 SQL 明确指定覆盖索引，保留其他语义和结果。",
             "", "| 迭代 | 全计划旧实现中位数 秒 | 全计划新实现中位数 秒 | 依据 |", "| --- | ---: | ---: | --- |"]
    for label, relative in [("初次投影", "app_mysql_acceptance_v1/queries"), ("补充覆盖索引", "app_mysql_query_v2"), ("指定已验收覆盖索引", "app_mysql_query_v3")]:
        data = json.loads((ROOT / "experiments" / relative / "query_comparison.json").read_text(encoding="utf-8"))
        if data["status"] == "verified":
            medians = {r["implementation"]: r["median"] for r in data["aggregates"] if r["kind"] == "all_plans"}
            text.append(f"| {label} | {medians['legacy']:.6f} | {medians['structured']:.6f} | {relative} |")
    text += ["", "分段诊断中，未录名单 SQL 正常计划约 4.871580 秒，指定索引约 0.224743 秒；这是一次 SQL 诊断，不是全运行统计中位数。执行计划和原始诊断见 app_plan_index_v1，最终结论使用上面的重复完整测量。",
             "", "## 写入成本与边界", "", "主系统每批保存全部考生过程和计划投影，增加了写入和存储成本。新验收报告的 scale_execute 包括四批计算、投影及四次事务保存，和历史单次最终结果落库的 write_and_commit 不是相同口径，不能直接相除。输入聚合和检查点做 zlib 压缩减少传输，但其 CPU 和存储取舍未单独测量。",
             "", "没有关闭 binlog，没有修改 redo 容量或其他全局参数。旧实验、两次统计退化报告、初始化连接失败及真实回滚验收均保留。",
             "", "原始报告：[最终查询对照](../experiments/app_mysql_query_v3/query_comparison.json)、[主系统完整验收](../experiments/app_mysql_acceptance_v1/acceptance.json)、[真实事务故障验收](../experiments/app_mysql_fault_v1/mysql_fault_recovery.json)。", ""]
    stats_before = ROOT / "experiments/app_web_statistics_index_v1/diagnosis_before.json"
    stats_after = ROOT / "experiments/app_web_statistics_index_v1/diagnosis_after.json"
    if stats_after.exists():
        before = json.loads(stats_before.read_text(encoding="utf-8"))
        after = json.loads(stats_after.read_text(encoding="utf-8"))
        text += ["", "## 后续界面汇总修正", "", "查询 v3 完成后，真实页面联通发现严格 GROUP BY 表达式和初中汇总回表问题。已修正分组表达式，并添加界面专用覆盖索引。下面只报告单次诊断，不称为正式三轮中位数。", "", "| 界面 SQL | 修改前单次秒数 | 修改后单次秒数 | 内容核对 |", "| --- | ---: | ---: | --- |"]
        for key, name in [("scores", "分数分布"), ("juniors", "初中汇总")]:
            text.append(f"| {name} | {before['queries'][key]['client_seconds']:.6f} | {after['queries'][key]['client_seconds']:.6f} | 全量结果一致 |")
        text += ["", "前述查询 v3 保留它执行时的源码与索引记录；后续界面修正没有改变投档、考生点查或全计划统计函数的语义。最终版本另外通过真实 MySQL 页面及完整统计导出验收，不修改旧报告 SHA。", ""]
    (DOCS / "性能实测.md").write_text("\n".join(text), encoding="utf-8")


def main():
    diagrams()
    performance_document()
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Inches(8.5), Inches(11)
    section.top_margin = section.bottom_margin = Inches(.75)
    section.left_margin = section.right_margin = Inches(.8)
    set_font(doc.styles["Normal"], 11)
    doc.styles["Normal"].paragraph_format.line_spacing = 1.25
    doc.styles["Normal"].paragraph_format.space_after = Pt(8)
    set_font(doc.styles["Title"], 21, True)
    set_font(doc.styles["Heading 1"], 15, True)
    set_font(doc.styles["Heading 2"], 12, True)
    section.header.paragraphs[0].text = "软件系统开发实践课程设计"
    section.header.paragraphs[0].runs[0].font.size = Pt(9)
    footer = section.footer.paragraphs[0]
    footer.alignment = 2
    footer.add_run("第 ")
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)
    footer.add_run(" 页")
    lines = (DOCS / "设计报告.md").read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("|"):
            block = []
            while index < len(lines) and lines[index].startswith("|"):
                block.append(lines[index]); index += 1
            add_table(doc, block)
            continue
        if line.startswith("# "):
            doc.add_paragraph(line[2:], "Title")
        elif line.startswith("## "):
            doc.add_paragraph(line[3:], "Heading 1")
        elif line.startswith("!["):
            match = re.fullmatch(r"!\[(.*?)\]\((.*?)\)", line)
            if match:
                p = doc.add_paragraph()
                p.alignment = 1
                p.add_run().add_picture(str(DOCS / match[2]), width=Inches(6.8))
                p.paragraph_format.keep_with_next = True
                caption = doc.add_paragraph(match[1])
                caption.alignment = 1
                caption.runs[0].font.size = Pt(10)
        elif line.strip():
            doc.add_paragraph(line.replace("`", "").replace("**", ""))
        index += 1
    doc.core_properties.title = "简化广州中考公办高中录取模拟系统设计报告"
    doc.core_properties.subject = "需求 设计 实现 测试与实验边界"
    doc.core_properties.author = "课程设计项目"
    # 捆绑默认模板可能带有 Title 段落的蓝色下边线，显式清除。
    for root in (doc._element, doc.styles.element):
        for border in root.xpath('.//w:pBdr'):
            border.getparent().remove(border)
    doc.save(DOCS / "设计报告.docx")
    print("已构建设计报告与三个设计图；DOCX 仍需渲染检查")


if __name__ == "__main__":
    main()
