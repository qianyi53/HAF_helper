# -*- coding: utf-8 -*-
"""M3 探测十：块数与 LLM 输入规模估算（分析用）"""
import json, re, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

data = json.load(open("data/labeled_docs.json", encoding="utf-8"))
docs = [d for d in data["documents"] if d["labels"]]

EST_CERTS_PER_UNIT = 1.4   # 同一单位常见"设计+制造"两证(实测 s5: 18 家 -> 25 证书块)
blocks = 0
pages = 0
detail = []
for d in docs:
    m = re.search(r"等\s*(\d+)\s*家单位", d["名称"])
    atts = len(d["附件及链接"] or [])
    if m:
        n = int(m.group(1))
        if "事件:变更" in d["labels"]:
            b = n                    # 变更: 一页一家(实测 s6)
            p = n
        else:
            b = round(n * EST_CERTS_PER_UNIT)
            p = round(n * 3.5) + 2
    else:
        b = max(1, atts)
        p = max(1, atts) * 3.5
    blocks += b
    pages += p
    detail.append((b, p))

print(f"域内文档 {len(docs)} 篇")
print(f"估算许可证/变更块总数: {blocks}")
print(f"估算总页数: {pages:.0f}")
print(f"按 2.5k tokens/块(文本+表格) 估算 P0 全量 LLM 输入: {blocks*2500/1e6:.1f}M tokens")
print(f"若仅 30% 块需 LLM 兜底: {blocks*0.3*2500/1e6:.1f}M tokens")
