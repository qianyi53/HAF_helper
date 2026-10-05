# -*- coding: utf-8 -*-
"""M3 探测六：分块锚点全量扫描 + 表头列名精化统计（分析用，不属于落地代码）"""
import glob, os, re, sys
from collections import Counter
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import pdfplumber

ANCHORS = {
    "证书首页(证件标题)":  r"^民用核安全设备(设计|制造|安装)许可证$",
    "附表页(附表)":        r"^附表$",
    "单位范围表标题行":    r"^.{4,45}(民用核(电)?安全设备).{0,12}(许可活动范围表|许可证)",
    "能力参数续页":        r"^(设计|制造)能力特征参数",
    "境外确认书条目":      r"^(一|二|三|四|五|六|七|八|九|十|十一|十二)、",
    "境外表一表二":        r"^表(一|二)",
}
print("### 分块锚点全量扫描")
for path in sorted(glob.glob("data/pdf/*.pdf")):
    fn = os.path.basename(path)
    with pdfplumber.open(path) as pdf:
        hits = {k: [] for k in ANCHORS}
        for i, page in enumerate(pdf.pages, 1):
            txt = (page.extract_text() or "").strip()
            first = txt.split("\n")[0].strip() if txt else ""
            for k, p in ANCHORS.items():
                if re.search(p, first):
                    hits[k].append(i)
        print(f"\n  [{fn}] {len(pdf.pages)} 页")
        for k, v in hits.items():
            if v:
                tail = "" if len(v) <= 14 else f" …(共{len(v)})"
                print(f"    {k}: 页 {v[:14]}{tail}")

# ---------- 表头列名精化统计（白名单口径）----------
HEADER_TOKENS = [
    "设备种类", "设备类别", "设备品种", "设备品种/设备名称", "设备名称", "核安全级别",
    "设计能力特征参数", "制造能力特征参数", "结构形式", "载荷等级", "典型设备名称",
    "公称通径", "设计压力", "设计温度", "主要关键工艺",
    "设计活动范围及完成形式", "制造活动范围及完成形式", "安装活动范围及完成形式",
    "活动场所", "备注", "民用核设施类别",
]
print("\n### 表头列名统计（白名单口径, 前2行）")
cnt = Counter()
per_file = {}
for path in sorted(glob.glob("data/pdf/*.pdf")):
    fn = os.path.basename(path)
    found = set()
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for tb in page.extract_tables():
                for r in tb[:2]:
                    for c in r:
                        if not c:
                            continue
                        flat = re.sub(r"\s+", "", c)
                        for tok in HEADER_TOKENS:
                            if tok in flat:
                                found.add(tok)
    per_file[fn] = found
    for t in found:
        cnt[t] += 1
for t, n in cnt.most_common():
    files = [f[:14] for f, s in per_file.items() if t in s]
    print(f"  {t:24s} {n}/10  {','.join(files)}")
