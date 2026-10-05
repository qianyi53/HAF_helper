# -*- coding: utf-8 -*-
"""M3 探测四：证书页文本 + 表格表头/行结构（分析用，不属于落地代码）"""
import sys, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import pdfplumber

def dump_text(path, pages, title):
    print("=" * 100)
    print(f"### {title} :: {path} 页 {pages}")
    with pdfplumber.open(path) as pdf:
        for i in pages:
            if i > len(pdf.pages):
                continue
            t = pdf.pages[i - 1].extract_text() or ""
            print(f"--- p{i} 文本 ---")
            print(t[:1200])

def dump_tables(path, pages, title, max_rows=6):
    print("=" * 100)
    print(f"### {title} :: {path} 表格 页 {pages}")
    with pdfplumber.open(path) as pdf:
        for i in pages:
            if i > len(pdf.pages):
                continue
            page = pdf.pages[i - 1]
            for ti, tb in enumerate(page.extract_tables()):
                print(f"--- p{i} 表{ti+1}: {len(tb)}行 x {len(tb[0]) if tb else 0}列 ---")
                for r in tb[:max_rows]:
                    cells = [ (c or "").replace("\n", "⏎")[:22] for c in r ]
                    print("   | " + " | ".join(cells))

MC = json.load(open("data/pdf/_manifest.json", encoding="utf-8"))
url2att = {}
for m in MC:
    url2att.setdefault(m["doc_id"], {"att": m["att_name"], "body": m["body"], "title": m["doc_title"], "labels": m["labels"]})

# 1) 颁发-设计单证: 证书页 + 附表
dump_text("data/pdf/s1_颁发_设计单证_0.pdf", [1], "颁发-设计许可证 证书页")
dump_tables("data/pdf/s1_颁发_设计单证_0.pdf", [3], "颁发-设计许可证 附表", 8)

# 2) 变更-单证范围表
dump_text("data/pdf/s3_变更_单证范围表_0.pdf", [1], "变更-制造范围表 全页文本")
dump_tables("data/pdf/s3_变更_单证范围表_0.pdf", [1], "变更-制造范围表 表", 8)

# 3) 变更-双证（设计）: 范围表 + 能力特征参数
dump_tables("data/pdf/s4_变更_双证_0.pdf", [1, 2], "变更-设计 范围表+能力参数", 6)

# 4) 延续-18家合一: 首证 6 页（证书页+附表+能力参数）
dump_text("data/pdf/s5_延续_18家合一_0.pdf", [1, 3], "延续18家 首证证书页+附表首页")
dump_tables("data/pdf/s5_延续_18家合一_0.pdf", [3, 4], "延续18家 附表+设计能力参数", 5)

# 5) 变更多单位（20家）: 第2页一家
dump_text("data/pdf/s6_变更_20家范围表_0.pdf", [2], "变更多单位 单家页文本")
dump_tables("data/pdf/s6_变更_20家范围表_0.pdf", [2], "变更多单位 单一表", 6)

# 6) 境外注册登记确认书
dump_text("data/pdf/s7_境外_单家_0.pdf", [1], "境外注册登记确认书 首页")
dump_tables("data/pdf/s7_境外_单家_0.pdf", [1], "境外确认书 表", 5)
dump_tables("data/pdf/s8_境外_多单位名单_1.pdf", [1, 3], "境外多单位确认书 表", 5)
