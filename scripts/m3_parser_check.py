# -*- coding: utf-8 -*-
"""L3 解析器在全部样本上的快速巡检（开发调试用）"""
import glob
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from haf.pdf_parser import parse_pdf  # noqa: E402

for path in sorted(glob.glob("data/pdf/*.pdf")):
    recs = parse_pdf(path, ctx={"att_name": os.path.basename(path), "doc_date": "2026-09-01"})
    print(f"\n===== {os.path.basename(path)} → {len(recs)} 条")
    for r in recs:
        rows = len(r["scope_items"])
        cap = sum(1 for s in r["scope_items"] if s.get("能力特征参数"))
        print(f"  {r['kind']:11s} | {str(r['cert_kind']):6s} | {str(r['cert_no'])[:22]:24s} "
              f"| {str(r['unit_name'])[:26]:28s} | p{r['page_start']}-{r['page_end']} "
              f"| 行{rows:3d} 带参数{cap:3d} | {r['extract_mode']}")
    # 首个范围行样例
    for r in recs:
        if r["scope_items"]:
            print("     样例行:", json.dumps(r["scope_items"][0], ensure_ascii=False)[:300])
            break
