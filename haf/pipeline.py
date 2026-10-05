# -*- coding: utf-8 -*-
"""haf/pipeline.py — 全流程编排（L2 打标 → L3 解析 → L4 建库）

用法:
  python -X utf8 -m haf.pipeline --stage l3 --limit 20          # 试跑：只解析 20 个附件（含 LLM 兜底）
  python -X utf8 -m haf.pipeline --stage l3 --no-llm            # 纯规则（不调 LLM）
  python -X utf8 -m haf.pipeline --stage l3 --llm-only          # 全部块走 LLM（对照实验）
  python -X utf8 -m haf.pipeline --stage l4 --csv               # 建库并导出 CSV
  python -X utf8 -m haf.pipeline --stage all --limit 20
产物:
  data/parsed_records.json   L3 记录 + 组装的模型（licenses/versions/scope_items/changes/registry_scopes）
  data/db/haf.db             L4 交付物（SQLite 单文件）
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime

from haf import chain, db_builder, pdf_fetch, pdf_parser, state as state_mod

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LABELED = os.path.join(ROOT, "data", "labeled_docs.json")
PARSED = os.path.join(ROOT, "data", "parsed_records.json")
DB_OUT = os.path.join(ROOT, "data", "db", "haf.db")


# ---------------- L4：文函主表行 ----------------
def docs_for_db(labeled_docs):
    out = []
    for d in labeled_docs:
        labels = d.get("labels") or []
        out.append({
            "doc_id": d.get("doc_id"),
            "标题": d.get("名称"),
            "文号": d.get("文号"),
            "分类": d.get("分类"),
            "发布机关": d.get("发布机关"),
            "生成日期": d.get("生成日期"),
            "成文日期": d.get("成文日期"),
            "事件标签": "、".join(l.split(":", 1)[1] for l in labels if l.startswith("事件:")),
            "官网链接": d.get("链接"),
            "正文": d.get("正文"),
            "附件数": len(d.get("附件及链接") or []),
            "已解析": 1 if labels else 0,
            "官网状态": d.get("site_status"),          # 撤稿标记（L1 产生）
            "爬取时间": d.get("crawled_at"),
        })
    return out


def missing_doc_ids(labeled_docs):
    """官网列表中已不见的文函（L1 的 site_status 标记）"""
    return {d["doc_id"] for d in labeled_docs if d.get("site_status") == "missing_in_list"}


def docs_index(labeled_docs):
    return {d["doc_id"]: {"官网链接": d.get("链接"), "标题": d.get("名称"),
                          "文号": d.get("文号"), "生成日期": d.get("生成日期"),
                          "成文日期": d.get("成文日期"),
                          "事件标签": [l.split(":", 1)[1] for l in d.get("labels", [])
                                    if l.startswith("事件:")]}
            for d in labeled_docs}


# ---------------- L3 ----------------
def run_l3(limit=0, use_llm=True, force_llm=False, delay=1.5, only_need_pdf=True,
           incremental=True, reparse=False, verbose=True):
    """L3：附件下载（增量）→ 解析（按内容指纹跳过未变附件）→ 版本链组装

    incremental=True 时复用 data/parsed_records.json 中未变附件的历史记录；
    reparse=True 强制重解析全部附件；limit 仅用于调试（只解析前 N 个新附件）。
    """
    labeled = json.load(open(LABELED, encoding="utf-8"))["documents"]
    by_doc = {d["doc_id"]: d for d in labeled}
    print(f"[L3] 域内文档 {sum(1 for d in labeled if d.get('labels'))} 篇；"
          f"need_pdf_parse {sum(1 for d in labeled if d.get('need_pdf_parse'))} 篇")

    idx = pdf_fetch.fetch_all(delay=delay, limit=limit, only_need=only_need_pdf, verbose=False)
    # 按「文函 × 附件」展开（同一附件 URL 可能被多篇文函引用，需各自成记录）
    ok_items = []
    for d in labeled:
        if not d.get("labels"):
            continue
        for i, a in enumerate(d.get("附件及链接") or [], 1):
            rec = idx.get(a["链接"])
            if rec and rec.get("状态") == "ok":
                ok_items.append(dict(rec, doc_id=d["doc_id"], 序号=i, 附件名称=a.get("名称")))
    ok_items.sort(key=lambda r: (r["doc_id"], r["序号"]))

    # 历史记录（用于增量复用）
    prev_records, prev_state = [], state_mod.load()
    if incremental and os.path.exists(PARSED):
        try:
            prev_records = json.load(open(PARSED, encoding="utf-8")).get("records", [])
        except Exception:  # noqa: BLE001
            prev_records = []
    prev_by_key = {}
    for r in prev_records:
        prev_by_key.setdefault((r.get("doc_id"), r.get("att_url")), []).append(r)

    client = None
    if use_llm:
        from haf.llm import LLMClient, load_config
        client = LLMClient(load_config(), verbose=False)
        print(f"[L3] LLM: {client.cfg.describe()} | thinking={client.cfg.thinking}")

    records, errors, reused, parsed = [], {}, 0, 0
    valid_keys = {state_mod.key(a["链接"], a["doc_id"]) for a in ok_items}
    t0 = time.time()
    for n, att in enumerate(ok_items, 1):
        url, doc = att["链接"], by_doc[att["doc_id"]]
        k = (doc["doc_id"], url)
        sk = state_mod.key(url, doc["doc_id"])
        srec = (prev_state.get("attachments") or {}).get(sk) or {}
        # 增量：同一（文函, 附件）指纹未变且模式一致 → 复用历史记录
        # （状态里 records=0 表示该附件本就无有效记录，如纯名单页，可直接跳过）
        fresh = (not reparse and not force_llm
                 and state_mod.is_fresh(prev_state, url, att.get("sha1"), doc["doc_id"], llm=use_llm))
        if fresh and (prev_by_key.get(k) or srec.get("records") == 0):
            records += prev_by_key.get(k, [])
            reused += 1
            continue
        if limit and parsed >= limit:
            records += prev_by_key.get(k, [])
            reused += 1
            continue
        ctx = {
            "doc_id": doc["doc_id"],
            "doc_title": doc.get("名称"),
            "doc_url": doc.get("链接"),
            "doc_date": doc.get("成文日期") or doc.get("生成日期"),
            "成文日期": doc.get("成文日期"),
            "att_name": att.get("附件名称"),
            "att_url": url,
        }
        try:
            recs = pdf_parser.parse_pdf(att["本地路径"], ctx=ctx, llm=client, force_llm=force_llm)
            for r in recs:
                r["附件本地路径"] = att["本地路径"]
            records += recs
            parsed += 1
            mode = "llm" if force_llm else ("rule+llm" if any(
                str(r.get("extract_mode", "")).startswith("llm") for r in recs) else "rule")
            state_mod.mark(prev_state, url, doc["doc_id"], att.get("sha1"), len(recs), mode,
                           llm=use_llm)
            if verbose:
                print(f"  [{n}/{len(ok_items)}] {doc['doc_id']} -> {len(recs)} 条")
        except Exception as e:  # noqa: BLE001
            errors[url] = str(e)[:300]
            print(f"  [!] 解析失败 {url[:70]}: {e}")

    removed_state = state_mod.prune(prev_state, valid_keys)
    cancel_refs = chain.extract_cancel_refs(labeled)
    miss = missing_doc_ids(labeled)
    model = chain.build_model(records, docs_index=docs_index(labeled), cancel_refs=cancel_refs,
                              missing_doc_ids=miss)
    payload = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "attachments_total": len(ok_items),
            "parsed": parsed,
            "reused": reused,
            "records": len(records),
            "errors": errors,
            "llm": (client.usage if client else None),
            "elapsed_seconds": round(time.time() - t0, 1),
        },
        "records": records,
        # documents（含正文）不在此落盘：L4 直接从 labeled_docs.json 读取，避免重复与体积膨胀
        **model,
    }
    with open(PARSED, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    state_mod.finish(prev_state, {"attachments_total": len(ok_items), "parsed": parsed,
                                  "reused": reused, "errors": len(errors),
                                  "pruned_state": len(removed_state)})
    state_mod.save(state_mod.STATE_PATH, prev_state)

    print(f"[L3] 附件 {len(ok_items)} | 新解析 {parsed} | 增量复用 {reused} | 失败 {len(errors)}")
    print(f"[L3] 注销引用 {len(cancel_refs)} 条 | 撤稿文函 {len(miss)} 篇")
    print(f"[L3] 记录 {len(records)} 条 | 许可证 {len(model['licenses'])} / 版本 {len(model['license_versions'])}"
          f" / 范围行 {len(model['scope_items'])} / 变更 {len(model['changes'])}"
          f" / 境外登记 {len(model['registry_scopes'])} -> {PARSED}")
    if client:
        print("[L3] " + client.summary())
    return payload


# ---------------- L4 ----------------
def run_l4(csv=False, in_path=PARSED, out_path=DB_OUT, labeled_path=LABELED):
    data = json.load(open(in_path, encoding="utf-8"))
    model = {k: data.get(k, []) for k in db_builder.TABLE_COLUMNS}
    # 文函主表直接取自 L2 产物（含正文），避免在 L3 文件里重复存储
    if os.path.exists(labeled_path):
        docs = docs_for_db(json.load(open(labeled_path, encoding="utf-8"))["documents"])
    else:
        docs = data.get("documents", [])
    stats, fk_errors = db_builder.build(model, docs, out_path, also_csv=csv)
    print("[L4] 建库完成:")
    for k, v in stats.items():
        print(f"    {k:22s} {v}")
    print("[L4] 外键校验: " + ("通过" if not fk_errors else f"{len(fk_errors)} 处问题 {fk_errors[:3]}"))
    print(f"[L4] 交付文件: {out_path}")
    return stats


def run_l1(full_scan=False, since=None, delay=None):
    """L1：调用官网增量爬虫（复用 scripts/nnsa_crawler.py，保持单一实现）"""
    script = os.path.join(ROOT, "scripts", "nnsa_crawler.py")
    cmd = [sys.executable, "-X", "utf8", script]
    if full_scan:
        cmd.append("--full-scan")
    if since:
        cmd += ["--since", since]
    if delay:
        cmd += ["--delay", str(delay)]
    print(f"[L1] 运行爬虫：nnsa_crawler.py {' '.join(cmd[4:]) or '(增量，沿用已存 cutoff)'}")
    subprocess.run(cmd, check=True)


def run_l2():
    """L2：全量重新打标（秒级，天然幂等）"""
    from haf import classifier
    src = os.path.join(ROOT, "data", "nnsa_policy_docs.json")
    print("[L2] 筛选打标 ...")
    return classifier.run(src, LABELED)


def run_weekly(csv=False, reparse=False, delay=1.5, full_scan=False, since=None):
    """M6 周更：L1 采集 → L2 打标 → L3 增量解析 → L4 重建交付库"""
    t0 = time.time()
    print("=" * 78)
    print(f"[weekly] 开始 {datetime.now().isoformat(timespec='seconds')}")
    run_l1(full_scan=full_scan, since=since, delay=delay)
    run_l2()
    run_l3(delay=delay, reparse=reparse, incremental=True, verbose=False)
    stats = run_l4(csv=csv)
    print(f"[weekly] 完成，用时 {round(time.time() - t0, 1)}s")
    return stats


def main():
    ap = argparse.ArgumentParser(description="HAF-agent 流水线（L1 采集 / L2 打标 / L3 解析 / L4 建库）")
    ap.add_argument("--stage", choices=["l1", "l2", "l3", "l4", "all", "weekly"], default="weekly",
                    help="weekly=一键周更（默认）；all=L2+L3+L4；l1~l4=单阶段")
    ap.add_argument("--limit", type=int, default=0, help="L3 只新解析前 N 个附件（调试）")
    ap.add_argument("--no-llm", action="store_true", help="L3 纯规则，不调用 LLM")
    ap.add_argument("--llm-only", action="store_true", help="L3 所有块都走 LLM（对照）")
    ap.add_argument("--reparse", action="store_true", help="L3 忽略增量状态，重解析全部附件")
    ap.add_argument("--delay", type=float, default=1.5)
    ap.add_argument("--csv", action="store_true", help="L4 额外导出 CSV")
    ap.add_argument("--full-scan", action="store_true", help="L1 全窗校验（含撤稿检测）")
    ap.add_argument("--since", help="L1 起始日期（改 cutoff 时用）")
    args = ap.parse_args()

    if args.stage == "l1":
        run_l1(full_scan=args.full_scan, since=args.since, delay=args.delay)
    elif args.stage == "l2":
        run_l2()
    elif args.stage == "l3":
        run_l3(limit=args.limit, use_llm=not args.no_llm, force_llm=args.llm_only,
               delay=args.delay, reparse=args.reparse)
    elif args.stage == "l4":
        run_l4(csv=args.csv)
    elif args.stage == "all":
        run_l2()
        run_l3(limit=args.limit, use_llm=not args.no_llm, force_llm=args.llm_only,
               delay=args.delay, reparse=args.reparse)
        run_l4(csv=args.csv)
    else:
        run_weekly(csv=args.csv, reparse=args.reparse, delay=args.delay,
                   full_scan=args.full_scan, since=args.since)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    main()
