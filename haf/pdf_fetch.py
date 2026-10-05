# -*- coding: utf-8 -*-
"""haf/pdf_fetch.py — L3 附件下载（限速、断点续传、按 doc_id 分目录 + 索引）

用法:
  python -X utf8 -m haf.pdf_fetch --limit 10          # 下载前 10 个附件（调试用）
  python -X utf8 -m haf.pdf_fetch                     # 下载全部（need_pdf_parse=true 的域内文档）
索引: data/pdf/_index.json —— [{doc_id, 序号, 附件名称, 链接, 本地路径, 字节数, 状态, 下载时间}]
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LABELED = os.path.join(ROOT, "data", "labeled_docs.json")
PDF_DIR = os.path.join(ROOT, "data", "pdf")
INDEX = os.path.join(PDF_DIR, "_index.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def safe_name(s, limit=48):
    s = re.sub(r"[\\/:*?\"<>|\s]+", "_", s or "")
    return s[:limit] or "att"


def load_index():
    if os.path.exists(INDEX):
        with open(INDEX, encoding="utf-8") as f:
            return {r["链接"]: r for r in json.load(f)}
    return {}


def save_index(idx):
    os.makedirs(PDF_DIR, exist_ok=True)
    with open(INDEX, "w", encoding="utf-8") as f:
        json.dump(list(idx.values()), f, ensure_ascii=False, indent=1)


def iter_attachments(docs, only_need=True):
    """产出 (doc, 序号, 附件) —— 默认只取 need_pdf_parse 的域内文档"""
    for d in docs:
        if not d.get("labels"):
            continue
        if only_need and not d.get("need_pdf_parse"):
            continue
        for i, a in enumerate(d.get("附件及链接") or [], 1):
            yield d, i, a


def download(url, path, retries=3, timeout=60):
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r, open(path, "wb") as f:
                f.write(r.read())
            return os.path.getsize(path)
        except Exception:  # noqa: BLE001
            if attempt == retries:
                raise
            time.sleep(2 * attempt)


def fetch_all(labeled_path=LABELED, out_dir=PDF_DIR, delay=1.5, limit=0, force=False,
              only_need=True, verbose=True):
    docs = json.load(open(labeled_path, encoding="utf-8"))["documents"]
    idx = load_index()
    os.makedirs(out_dir, exist_ok=True)
    items = list(iter_attachments(docs, only_need=only_need))
    if limit:
        items = items[:limit]
    done = failed = 0
    for d, i, a in items:
        url = a["链接"]
        ext = url.rsplit(".", 1)[-1].lower().split("?")[0]
        if ext not in ("pdf", "doc", "docx"):
            continue
        rec = idx.get(url)
        sub = os.path.join(out_dir, d["doc_id"])
        fn = f"{i:02d}_{safe_name(a.get('名称'))}.{ext}"
        path = rec["本地路径"] if rec and os.path.exists(rec.get("本地路径", "")) else os.path.join(sub, fn)
        if rec and rec.get("状态") == "ok" and os.path.exists(path) and not force:
            done += 1
            continue
        os.makedirs(sub, exist_ok=True)
        try:
            size = download(url, path)
            with open(path, "rb") as f:
                header = f.read(5)
            if ext == "pdf" and header != b"%PDF-":
                # 链接后缀是 .pdf 但实际不是 PDF（实测有 1 个），直接标记，避免每轮重试
                idx[url] = {"doc_id": d["doc_id"], "序号": i, "附件名称": a.get("名称"), "链接": url,
                            "本地路径": path, "字节数": size, "状态": "非PDF",
                            "说明": f"文件头为 {header!r}，非 PDF",
                            "下载时间": datetime.now().isoformat(timespec="seconds")}
                print(f"  [!] 非 PDF 文件，已标记跳过: {url[-60:]}")
                continue
            idx[url] = {"doc_id": d["doc_id"], "序号": i, "附件名称": a.get("名称"),
                        "链接": url, "本地路径": path, "字节数": size, "状态": "ok",
                        "sha1": hashlib.sha1(open(path, "rb").read()).hexdigest()[:12],
                        "下载时间": datetime.now().isoformat(timespec="seconds")}
            done += 1
            if verbose:
                print(f"  [{done}/{len(items)}] {d['doc_id']} #{i} {size/1024:.0f}KB "
                      f"{str(a.get('名称'))[:36]}")
            time.sleep(delay)
        except Exception as e:  # noqa: BLE001
            failed += 1
            idx[url] = {"doc_id": d["doc_id"], "序号": i, "附件名称": a.get("名称"), "链接": url,
                        "本地路径": path, "状态": "failed", "错误": str(e)[:200],
                        "下载时间": datetime.now().isoformat(timespec="seconds")}
            print(f"  [!] 下载失败 {url[:80]}: {e}")
        if done % 20 == 0:
            save_index(idx)
    save_index(idx)
    ok = sum(1 for r in idx.values() if r.get("状态") == "ok")
    print(f"[i] 附件下载完成: 本轮 ok {done} / failed {failed} | 索引累计可用 {ok} 个 -> {INDEX}")
    return idx


def main():
    ap = argparse.ArgumentParser(description="L3 附件下载")
    ap.add_argument("--limit", type=int, default=0, help="只下载前 N 个（调试）")
    ap.add_argument("--delay", type=float, default=1.5)
    ap.add_argument("--force", action="store_true", help="重新下载已存在的附件")
    ap.add_argument("--all-labeled", action="store_true", help="不限 need_pdf_parse")
    args = ap.parse_args()
    fetch_all(delay=args.delay, limit=args.limit, force=args.force,
              only_need=not args.all_labeled)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    main()
