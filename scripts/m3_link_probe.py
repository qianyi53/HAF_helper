# -*- coding: utf-8 -*-
"""M3 探测九：证字号可提取率（附件名/正文）+ 页数粗估（分析用）"""
import json, re, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

data = json.load(open("data/labeled_docs.json", encoding="utf-8"))
docs = [d for d in data["documents"] if d["labels"]]
CERT = re.compile(r"国核安证字\s*[SZA]?\s*[（(]\s*\d{2}\s*[）)]\s*\d+\s*号")

n_att_name = n_body = n_either = 0
for d in docs:
    in_att = any(CERT.search(a["名称"]) for a in d["附件及链接"] or [])
    in_body = bool(CERT.search(d["正文"]))
    n_att_name += in_att
    n_body += in_body
    n_either += (in_att or in_body)
print(f"### 证字号可提取率（域内 {len(docs)} 篇）")
print(f"  附件名含证字号: {n_att_name} | 正文含证字号: {n_body} | 任一可得: {n_either} ({n_either/len(docs):.0%})")

by_ev = {}
for d in docs:
    for e in [l.split(":", 1)[1] for l in d["labels"] if l.startswith("事件:")]:
        by_ev.setdefault(e, []).append(d)
for e, lst in sorted(by_ev.items(), key=lambda x: -len(x[1])):
    a = sum(1 for d in lst if any(CERT.search(x["名称"]) for x in d["附件及链接"] or []))
    b = sum(1 for d in lst if CERT.search(d["正文"]))
    print(f"  [{e}] {len(lst)} 篇 -> 附件名 {a} / 正文 {b}")

# 页数粗估
single = [d for d in docs if not re.search(r"等\s*\d+\s*家单位", d["名称"])]
multi = [d for d in docs if re.search(r"等\s*\d+\s*家单位", d["名称"])]
est = 0
for d in single:
    est += 3.5 * max(1, len(d["附件及链接"] or []))
for d in multi:
    n = int(re.search(r"等\s*(\d+)\s*家单位", d["名称"]).group(1))
    est += 3.5 * n + 2
print(f"\n### 页数粗估（单证≈3.5页/附件, 多单位≈3.5页/家+2）")
print(f"  单单位 {len(single)} 篇 + 多单位 {len(multi)} 篇 -> 约 {est:.0f} 页")
