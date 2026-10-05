# -*- coding: utf-8 -*-
"""M1 特征分析（最终版）：复现《M1筛选特征分析报告》中的全部统计数字

用法: python scripts/m1_analysis.py
"""
import json
import re
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DATA = "data/nnsa_policy_docs.json"

# 域门控: 核安全设备 / 注册登记 / 单位资质 三族关键词
DOMAIN = re.compile(r"核安全设备|注册登记|单位资质")
# 事件优先级: 不予延续 > 吊销/注销 > 变更 > 延续 > 颁发/首次
EVENTS = [
    ("不予延续", r"不予延续"),
    ("吊销注销", r"吊销|注销"),
    ("变更",     r"变更"),
    ("延续",     r"延续"),
    ("颁发",     r"颁发|批准|准予"),
]
# 内嵌事件(句级)
EMBEDDED = [
    ("注销原证",         r".{0,30}同时注销.{0,50}"),
    ("注册登记范围变更", r".{0,20}注册登记范围变更"),
    ("单位名称变更",     r".{0,20}(单位名称变更|公司名称变更|更名)"),
]
ACT_RE = re.compile(r"设计|制造|安装|无损检验")


def main():
    docs = json.load(open(DATA, encoding="utf-8"))["documents"]
    for d in docs:
        d["_text"] = d["名称"] + "\n" + (d.get("正文") or "")

    in_d = [d for d in docs if DOMAIN.search(d["_text"])]
    out_d = [d for d in docs if not DOMAIN.search(d["_text"])]
    print(f"全量 {len(docs)} | 域内 {len(in_d)} | 域外 {len(out_d)}")

    # 事件分类: 文档级事件只看标题(标题措辞模板化);
    # 内嵌事件(同时注销/范围变更/名称变更)单独句级抽取, 不参与文档级分类
    ev = Counter()
    for d in in_d:
        tag = None
        for t, p in EVENTS:
            if re.search(p, d["名称"]):
                tag = t
                break
        ev[tag or "标题未命中"] += 1
    print("事件分布:", dict(ev))

    # 内嵌事件
    emb = Counter()
    for d in in_d:
        for t, p in EMBEDDED:
            if re.search(p, d["_text"]):
                emb[t] += 1
    print("内嵌事件:", dict(emb))

    # 活动种类注明情况
    act = Counter()
    for d in in_d:
        act["注明" if ACT_RE.search(d["名称"]) else "未注明"] += 1
    print("活动种类(标题):", dict(act))

    # 域外疑似(反向排查)
    sus = [d for d in out_d if re.search(r"许可|变更|注册|资质", d["名称"])]
    print(f"域外含许可类字样(应全部排除): {len(sus)}")
    for d in sus[:10]:
        print(f"    {d['生成日期']}  {d['名称'][:50]}")


if __name__ == "__main__":
    main()
