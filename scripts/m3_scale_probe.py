# -*- coding: utf-8 -*-
"""M3 探测八：附件类型分布 / 多单位合一规模 / 变更正文全文样例（分析用）"""
import json, re, sys
from collections import Counter
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

data = json.load(open("data/labeled_docs.json", encoding="utf-8"))
docs = [d for d in data["documents"] if d["labels"]]

# 1. 附件扩展名分布
ext = Counter()
n_att = 0
for d in docs:
    for a in d["附件及链接"] or []:
        ext[a["链接"].rsplit(".", 1)[-1].lower()] += 1
        n_att += 1
print(f"### 1. 附件文件类型（共 {n_att} 个附件 / {len(docs)} 篇域内文档）")
print("   ", dict(ext.most_common()))

# 2. 多单位合一规模（标题含"等N家单位"）
multi = [d for d in docs if re.search(r"等\s*\d+\s*家单位", d["名称"])]
nums = [int(re.search(r"等\s*(\d+)\s*家单位", d["名称"]).group(1)) for d in multi]
print(f"\n### 2. 多单位合一篇数：{len(multi)}/{len(docs)}（占 {len(multi)/len(docs):.0%}）")
if nums:
    print(f"   '等N家单位' N 分布: {sorted(Counter(nums).items())[:14]}")
    print(f"   N 合计 {sum(nums)} 家单位, 平均 {sum(nums)/len(nums):.1f} 家/篇, 最大 {max(nums)}")

# 3. 单单位 vs 多单位（无"等N家"的标题 = 单单位）
print(f"   单单位文函: {len(docs)-len(multi)} 篇")

# 4. 变更正文全文样例
print("\n### 3. 变更类正文全文样例（2 篇）")
n = 0
for d in docs:
    if "事件:变更" in d["labels"] and re.search(r"变更为附件", d["正文"]):
        print(f"\n  ---- {d['名称'][:60]} ----")
        print("  " + (d["正文"][:700]).replace("\n", "\n  "))
        n += 1
        if n >= 2:
            break

# 5. 延续类正文是否列出单位清单
print("\n### 4. 延续类正文单位清单可得性")
renew = [d for d in docs if "事件:延续" in d["labels"]]
listed = sum(1 for d in renew if re.search(r"[^\s、，。]{4,}、[^\s、，。]{4,}", d["正文"]))
print(f"   延续 {len(renew)} 篇, 正文含单位清单迹象 {listed} 篇")
if renew:
    print("   样例正文片段:")
    print("   " + renew[0]["正文"][:420].replace("\n", "\n   "))
