# -*- coding: utf-8 -*-
"""M3 探测一：域内文档"正文可得信息"与"必须看附件"的边界（分析用，不属于落地代码）"""
import json, re, sys
from collections import Counter
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

data = json.load(open("data/labeled_docs.json", encoding="utf-8"))
docs = [d for d in data["documents"] if d["labels"]]   # 只看 L2 已打标签（提取范围内）
print(f"L2 域内 {len(docs)} 篇；其中有附件 {sum(1 for d in docs if d['附件及链接'])} 篇\n")

def ev(doc):
    return [lb.split(":", 1)[1] for lb in doc["labels"] if lb.startswith("事件:")]

# ---- 正文信号探测 ----
PATTERNS = {
    "正文含'许可证编号/证字号'": r"国核安证字|许可证编号|证书编号|编号[:：]",
    "正文含'许可证有效期至'":     r"有效期(至|为)",
    "正文含'许可活动范围'":       r"许可活动范围",
    "正文含'变更'且带前后对比":   r"变更(前|后)",
    "正文含单位列表(、分隔≥3项)":  r"[^\s、，。]{4,}、[^\s、，。]{4,}、[^\s、，。]{4,}",
    "正文含'附件'引用":           r"见附件|附件[:：1-9]",
}
print("=== 正文信号按事件类型统计 ===")
by_ev = {}
for d in docs:
    for e in ev(d) or ["(无事件)"]:
        by_ev.setdefault(e, []).append(d)
for e, lst in sorted(by_ev.items(), key=lambda x: -len(x[1])):
    print(f"\n[{e}] {len(lst)} 篇")
    for name, p in PATTERNS.items():
        n = sum(1 for d in lst if re.search(p, d.get("正文") or ""))
        print(f"    {name}: {n}/{len(lst)}")

# ---- 变更类正文样例（看变更内容是否在正文）----
print("\n=== 变更类正文片段样例(含'变更'句, 各取2条) ===")
n = 0
for d in docs:
    if "变更" in ev(d):
        for m in re.finditer(r"[^\n]{0,60}变更[^\n]{0,80}", d["正文"]):
            print(f"  [{d['名称'][:28]}] {m.group(0)[:130]}")
            break
        n += 1
        if n >= 5:
            break

# ---- 附件文件名/名称统计（看附件类型）----
print("\n=== 附件名称样例 ===")
for d in docs:
    for a in (d["附件及链接"] or [])[:2]:
        print(f"  [{d['名称'][:24]}] {a['名称'][:40]} | {a['链接'].rsplit('.', 1)[-1]}")
