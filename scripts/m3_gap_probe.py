# -*- coding: utf-8 -*-
"""M3 探测七：有效期位置 / 变更内容描述 / 证字号样式 / 文号 / 一证一块边界（分析用）"""
import glob, json, os, re, sys
from collections import Counter
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import pdfplumber

# ---------- 1. PDF 中"有效期"位置 ----------
print("### 1. 样本 PDF 中'有效期'出现位置")
for path in sorted(glob.glob("data/pdf/*.pdf")):
    with pdfplumber.open(path) as pdf:
        hits = []
        for i, page in enumerate(pdf.pages, 1):
            t = page.extract_text() or ""
            for m in re.finditer(r"[^\n]{0,22}有效期[^\n]{0,40}", t):
                hits.append(f"p{i}:{m.group(0).strip()}")
        if hits:
            print(f"  [{os.path.basename(path)[:26]}] {hits[:3]}")

# ---------- 2. 证字号样式（全样本）----------
print("\n### 2. 证字号样式")
pat = re.compile(r"国核安证字\s*[A-Za-z]?\s*[（(]\s*\d{2,4}\s*[）)]\s*\d+\s*号|国核安证字\s*\d+")
styles = Counter()
for path in sorted(glob.glob("data/pdf/*.pdf")):
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            t = page.extract_text() or ""
            for m in pat.finditer(t):
                styles[re.sub(r"\s+", "", m.group(0))] += 1
print("  样例:", list(styles)[:12])
print("  共计", len(styles), "个不同证字号")

# ---------- 3. 变更类正文是否描述变更内容 ----------
print("\n### 3. 变更类正文片段（含'变更前/变更后/变更为'）")
data = json.load(open("data/labeled_docs.json", encoding="utf-8"))
chg = [d for d in data["documents"] if "事件:变更" in d["labels"]]
print(f"  变更类共 {len(chg)} 篇")
n_desc = 0
for d in chg:
    body = d["正文"]
    ms = re.findall(r"[^\n]{0,40}(?:变更前|变更后|变更为|同意.{0,6}变更)[^\n]{0,60}", body)
    if ms:
        n_desc += 1
        if n_desc <= 6:
            print(f"  [{d['名称'][:30]}]")
            for x in ms[:2]:
                print(f"      {x.strip()[:120]}")
print(f"  正文含变更描述片段: {n_desc}/{len(chg)}")

# ---------- 4. 文号统计（域内）----------
print("\n### 4. 域内文号可得性")
docs = [d for d in data["documents"] if d["labels"]]
wholen = [d for d in docs if d.get("文号")]
print(f"  有文号 {len(wholen)}/{len(docs)}")
pat2 = re.compile(r"[环核][^\s]{0,8}[〔\[［]\s*\d{4}\s*[〕\]］]\s*\d+\s*号")
n2 = sum(1 for d in docs if pat2.search(d["正文"]))
print(f"  正文中可正则抽到的文号: {n2}/{len(docs)}")

# ---------- 5. s5 一证一块的边界精确推导 ----------
print("\n### 5. s5_延续_18家合一 一证一块边界（证书页/附表页配对）")
path = "data/pdf/s5_延续_18家合一_0.pdf"
with pdfplumber.open(path) as pdf:
    cert_pages, fu_pages = [], []
    for i, page in enumerate(pdf.pages, 1):
        t = (page.extract_text() or "").strip()
        if re.match(r"民用核安全设备(设计|制造|安装)许可证", t.split("\n")[0].strip()):
            cert_pages.append(i)
        if t.split("\n")[0].strip() == "附表":
            fu_pages.append(i)
    print(f"  证书页 {len(cert_pages)}: {cert_pages}")
    print(f"  附表页 {len(fu_pages)}: {fu_pages}")
    # 按证书页切块: 下一证书页前一页为块尾
    blocks = []
    for k, cp in enumerate(cert_pages):
        end = (cert_pages[k + 1] - 1) if k + 1 < len(cert_pages) else len(pdf.pages)
        blocks.append((cp, end, end - cp + 1))
    print(f"  块数 {len(blocks)}，页数分布: {Counter(b[2] for b in blocks).most_common()}")
    print(f"  前6块: {blocks[:6]}")
