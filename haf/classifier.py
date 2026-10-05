# -*- coding: utf-8 -*-
"""haf/classifier.py — L2 筛选打标：域门控 → 多标签打标 → 内嵌事件抽取

用法:
  python -m haf.classifier                       # 读 data/nnsa_policy_docs.json → data/labeled_docs.json
  python -m haf.classifier --limit 20            # 调试: 只看前20篇的判定明细
  python -m haf.classifier --out xxx.json        # 指定输出

M1 → M2 数据接口（泛用契约, 详见 docs/02-架构设计.md §3.2, 后续不建议大改）:
  1. doc_id 唯一键贯穿全程, 原始字段全部透传, 打标结果只增不改;
  2. labels: 排序去重的标签数组 —— ["事件:颁发", "事件:注销", "证件类型:许可证", "活动种类:制造"]
  3. match_reasons: 逐条命中依据 (含规则名/匹配位置/原句摘录), 供人工抽检回溯;
  4. need_pdf_parse: 标题未注明活动种类, 或事件含颁发/延续 → true (M3 只处理该子集);
  5. excluded: 域外文档不丢弃, 也写入输出并标注排除原因, 保证全量可追溯可重跑。
"""
import argparse
import json
import os
import sys
from datetime import datetime

from haf import rules

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def label(tag, value):
    """统一标签格式: '类别:值'"""
    return f"{tag}:{value}"


def classify_doc(doc: dict) -> dict:
    """单篇文档打标。返回 (labels, reasons, need_pdf_parse)

    设计: 纯函数, 不修改入参; 文档级事件只看标题(见 rules.py 说明),
    内嵌事件从正文句级抽取后与文档级事件合并入同一标签族(裁定 Q-L2-5)。
    """
    title = doc.get("名称", "")
    body = doc.get("正文") or ""
    text = title + "\n" + body
    labels, reasons = [], []

    # ---- 域门控 ----
    if not rules.DOMAIN_GATE.search(text):
        return [], ["域门控未命中: 排除"], False

    # ---- 证件类型 (标题优先, 正文兜底; 先注册登记后许可证) ----
    cert = None
    for name, pat in rules.CERT_TYPE_RULES:
        where = None
        if pat.search(title):
            where = "标题"
        elif pat.search(body):
            where = "正文"
        if where:
            cert = name
            reasons.append(f"证件类型[{name}]: {where}命中")
            break

    # ---- 文档级事件 (仅标题, 依优先级; 可命中多个不同事件, 同名去重) ----
    title_events = []
    for name, pat in rules.TITLE_EVENT_RULES:
        if pat.search(title) and name not in title_events:
            title_events.append(name)
            reasons.append(f"事件[{name}]: 标题命中 '{title[:50]}'")

    # ---- 内嵌事件 (正文句级, 可多个) ----
    embedded_events = []
    for name, pat, note in rules.EMBEDDED_EVENT_RULES:
        m = pat.search(body)
        if m:
            embedded_events.append(name)
            frag = re_excerpt(m)
            reasons.append(f"事件[{name}]: 正文内嵌命中({note}) '{frag}'")

    # ---- 合并标签族 (裁定 Q-L2-5: 内嵌与非内嵌合并) ----
    events = title_events + [e for e in embedded_events if e not in title_events]

    # ---- 活动种类 (标题; 未注明留空待 L3) ----
    activities = [n for n, p in rules.ACTIVITY_RULES if p.search(title)]
    if activities:
        reasons.append(f"活动种类: 标题命中 {activities}")
    else:
        reasons.append("活动种类: 标题未注明, 留空待 L3 解析附件")

    # ---- 标签数组 (排序保证输出稳定, 便于 diff/测试) ----
    for e in events:
        labels.append(label("事件", e))
    if cert:
        labels.append(label("证件类型", cert))
    for a in activities:
        labels.append(label("活动种类", a))
    labels = sorted(set(labels))

    # ---- 是否需要 PDF 解析 ----
    # 原则: 明细(证照/变更前后对比)可能在附件 → 有附件即标记解析(M3 按需跳过无价值件);
    # 变更类 70% 带附件, 且无附件时变更明细在正文, L3 解析正文即可, 不在此处猜
    need_pdf = bool(doc.get("附件及链接"))

    return labels, reasons, need_pdf


def re_excerpt(m, limit=40):
    """从匹配对象取上下文摘录(压空白, 截断)"""
    s = re_norm(m.group(0))
    return s[:limit]


def re_norm(s):
    import re as _re
    return _re.sub(r"\s+", " ", s)


def run(in_path: str, out_path: str, limit: int = 0, verbose: bool = False):
    with open(in_path, encoding="utf-8") as f:
        raw = json.load(f)
    docs = raw.get("documents", [])
    if limit:
        docs = docs[:limit]

    out_docs, stats = [], {"域内": 0, "域外": 0}
    event_stat = {}
    for d in docs:
        labels, reasons, need_pdf = classify_doc(d)
        if not labels:
            stats["域外"] += 1
        else:
            stats["域内"] += 1
            for lb in labels:
                if lb.startswith("事件:"):
                    event_stat[lb] = event_stat.get(lb, 0) + 1
        rec = dict(d)  # 原始字段全部透传
        rec["labels"] = labels
        rec["match_reasons"] = reasons
        rec["need_pdf_parse"] = need_pdf
        out_docs.append(rec)
        if verbose:
            att = len(d.get("附件及链接") or [])
            print(f"  [{'域内' if labels else '域外'}][附件x{att}][PDF:{'Y' if need_pdf else 'N'}] "
                  f"{d.get('生成日期', '')} {d.get('名称', '')[:46]}")
            if labels:
                print(f"      labels={labels}")

    out = {
        "meta": {
            "source": in_path,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "rules_version": rules.RULES_VERSION,
            "input_count": len(docs),
            "stats": dict(stats, event_stat=event_stat,
                          need_pdf_parse=sum(1 for r in out_docs if r["need_pdf_parse"])),
        },
        "documents": out_docs,
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print(f"[i] 输入 {len(docs)} 篇 -> 域内 {stats['域内']} / 域外 {stats['域外']}")
    print(f"[i] 事件分布: {event_stat}")
    print(f"[i] need_pdf_parse: {out['meta']['stats']['need_pdf_parse']} 篇")
    print(f"[i] 写出: {out_path}")
    return out


def main():
    ap = argparse.ArgumentParser(description="L2 筛选打标")
    ap.add_argument("--in", dest="in_path", default=os.path.join(ROOT, "data", "nnsa_policy_docs.json"))
    ap.add_argument("--out", dest="out_path", default=os.path.join(ROOT, "data", "labeled_docs.json"))
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 篇(调试)")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印每篇判定")
    args = ap.parse_args()
    run(args.in_path, args.out_path, args.limit, args.verbose)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
