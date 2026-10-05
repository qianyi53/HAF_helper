# -*- coding: utf-8 -*-
"""M3 探测五：安装类/境外确认书字段 + 全样本表头列名统计 + 分块锚点扫描（分析用）"""
import glob, os, re, sys
from collections import Counter
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import pdfplumber

# ---------- 1. 安装类附表 + 境外确认书 ----------
def show(path, pages, title, rows=8):
    print("=" * 100)
    print(f"### {title} :: {os.path.basename(path)}")
    with pdfplumber.open(path) as pdf:
        for i in pages:
            if i > len(pdf.pages):
                continue
            page = pdf.pages[i - 1]
            print(f"--- p{i} 文本 ---\n{(page.extract_text() or '')[:900]}")
            for tb in page.extract_tables():
                print(f"--- p{i} 表({len(tb)}行x{len(tb[0])}列) ---")
                for r in tb[:rows]:
                    print("   | " + " | ".join((c or "").replace("\n", "⏎")[:20] for c in r))

show("data/pdf/s2_颁发_安装单证_0.pdf", [1, 3], "颁发-安装许可证 证书页+附表")
show("data/pdf/s7_境外_单家_0.pdf", [1], "境外注册登记确认书(单家)")

# ---------- 2. 全样本表头列名统计 ----------
print("\n" + "=" * 100)
print("### 全样本表头列名统计（合并单元格已去空）")
col_counter = Counter()
file_cols = {}
for path in sorted(glob.glob("data/pdf/*.pdf")):
    fn = os.path.basename(path)
    cols = set()
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for tb in page.extract_tables():
                for r in tb[:2]:      # 表头最多2行
                    for c in r:
                        if c:
                            for part in re.split(r"[\n、,，]", c):
                                part = re.sub(r"\s+", "", part)
                                if 1 < len(part) <= 12:
                                    cols.add(part)
    file_cols[fn] = cols
    for c in cols:
        col_counter[c] += 1

for c, n in col_counter.most_common(40):
    files = [f[:12] for f, s in file_cols.items() if c in s]
    print(f"  {c:22s} 出现于 {n}/10 文件: {','.join(files[:6])}")

# ---------- 3. 分块锚点扫描 ----------
print("\n" + "=" * 100)
print("### 分块锚点扫描（页首行模式）")
ANCHORS = {
    "证书首页(证件标题)": r"^民用核安全设备(设计|制造|安装)许可证",
    "附表页":             r"^附表",
    "能力参数续页":       r"^(设计|制造)能力特征参数",
    "单位范围表页":       r"^[^\s]{4,40}(民用核(电)?安全设备).{0,10}(许可活动范围表|许可证)",
    "境外确认书条目":     r"^(一|二|三|四|五|六|七|八|九|十)、",
}
for fn, _ in sorted(file_cols.items()):
    path = os.path.join("data/pdf", fn)
    with pdfplumber.open(path) as pdf:
        hits = {k: [] for k in ANCHORS}
        for i, page in enumerate(pdf.pages, 1):
            txt = (page.extract_text() or "").strip()
            first = txt.split("\n")[0].strip() if txt else ""
            for k, p in ANCHORS.items():
                if re.search(p, first):
                    hits[k].append(i)
        print(f"\n  [{fn}] 共 {len(pdf.pages)} 页")
        for k, v in hits.items():
            if v:
                print(f"    {k}: {len(v)} 处 -> 页 {v[:12]}{' ...' if len(v) > 12 else ''}")
