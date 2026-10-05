# -*- coding: utf-8 -*-
"""
nnsa_crawler.py — 国家核安全局"核安全文件"栏目爬虫（增量维护 JSON 表）

栏目: https://nnsa.mee.gov.cn/zcwj/haqjwj/wj/  (index.html ~ index_199.html, 按日期倒序, 约3000篇)

功能:
  1. 抓取 cutoff(含) 之后全部文件的详情, 字段:
     名称 / 分类 / 发布机关 / 生成日期 / 成文日期 / 文号 / 正文 / 附件及链接
  2. 存为一个 JSON 表(默认 data/nnsa_policy_docs.json), "documents" 为数组,
     按生成日期倒序; meta 里记录 cutoff 与上次运行时间
  3. 增量: 再次运行先读已有 JSON, 只抓官网新增的文档; 默认只扫描到
     max(cutoff, 上次运行日) 边界即停; --full-scan 强制从 cutoff 起全窗校验
  4. 一致性: 官网撤稿(列表中消失)且日期在本次扫描范围内的文档, 会标记
     "site_status": "missing_in_list"(不自动删除); --remove-missing 才真正移除

用法:
  python nnsa_crawler.py --since 2026-01-01     # 首次运行, 指定起始时间
  python nnsa_crawler.py                        # 增量运行, 沿用 JSON 里已存的 cutoff
  python nnsa_crawler.py --full-scan            # 全窗校验(cutoff 起逐页核对)
  python nnsa_crawler.py --remove-missing --prune

常用参数:
  --since YYYY-MM-DD   起始时间(含); 已有 JSON 时默认沿用其中记录
  --out PATH           目标 JSON 路径, 默认 data/nnsa_policy_docs.json
  --delay 秒           请求间隔, 默认 1.5
  --max-pages N        列表页扫描上限, 默认 250
  --limit N            本次最多抓取 N 篇详情(调试用)
  --full-scan          从 cutoff 起全窗扫描(校验撤稿), 而不是只扫到上次运行边界
  --refresh            重抓窗口内已有文档的详情(修复用)
  --remove-missing     删除官网已撤稿的文档
  --prune              删除生成日期早于 cutoff 的文档(当 cutoff 被改晚时用)
"""
import argparse
import json
import os
import re
import sys
import tempfile
import time
import urllib.request
from datetime import date, datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_LIST = "https://nnsa.mee.gov.cn/zcwj/haqjwj/wj/{}"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 工作区根目录
DEFAULT_OUT = os.path.join(_ROOT, "data", "nnsa_policy_docs.json")
AGENCY = "国家核安全局"

LIST_ITEM_RE = re.compile(
    r'<span class="date">\s*(\d{4}-\d{2}-\d{2})\s*</span>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
DOC_ID_RE = re.compile(r'(t\d{8}_\d+)')
WHOLENO_RE = re.compile(r'[\u4e00-\u9fa5A-Za-z]{1,14}\s*[〔\[［]\s*\d{4}\s*[〕\]］]\s*\d{1,5}\s*号')
DATE_CN_RE = re.compile(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日')
DIV_TOK_RE = re.compile(r'<div\b|</div>')


def fetch(url, timeout=25, retries=3, delay=1.5):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="ignore")
        except Exception as e:  # noqa: BLE001
            last = e
            if i < retries - 1:
                time.sleep(2 + 3 * i)
    raise RuntimeError(f"GET {url} 失败: {last}")


def norm_url(url):
    url = url.strip()
    if url.startswith("http://www.mee.gov.cn"):
        url = "https://www.mee.gov.cn" + url[len("http://www.mee.gov.cn"):]
    return url.split("#", 1)[0]


def doc_id_of(url):
    m = DOC_ID_RE.search(url)
    return m.group(1) if m else url


def parse_list(html):
    """返回 [(date, url, title), ...]"""
    items = []
    for m in LIST_ITEM_RE.finditer(html):
        d, href, title = m.group(1), m.group(2), m.group(3)
        title = re.sub(r"<[^>]+>", "", title)
        title = re.sub(r"\s+", " ", title).strip()
        items.append((d, norm_url(href), title))
    return items


def extract_containers(html, marker='class="content_body_box"'):
    """按 div 深度配对完整取出容器内容; 返回所有出现的容器(页面可能有多处模板)"""
    out = []
    pos = 0
    while True:
        i = html.find(marker, pos)
        if i < 0:
            break
        j = html.find(">", i)
        if j < 0:
            break
        j += 1
        depth, k, end = 1, j, len(html)
        while k < len(html) and k - j < 800000:
            m = DIV_TOK_RE.search(html, k)
            if not m:
                break
            if m.group(0) == "</div>":
                depth -= 1
                if depth == 0:
                    end = m.start()
                    break
            else:
                depth += 1
            k = m.end()
        out.append(html[j:end])
        pos = end if end > pos else i + len(marker)
    return out


def html_to_text(h):
    h = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1>", " ", h)
    h = re.sub(r"(?s)<!--.*?-->", " ", h)
    h = re.sub(r"(?i)<br\s*/?>", "\n", h)
    h = re.sub(r"(?i)</(p|div|tr|li|h[1-6]|td)>", "\n", h)
    h = re.sub(r"<[^>]+>", "", h)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&quot;", '"'), ("&#160;", " "), ("&ldquo;", "\u201c"), ("&rdquo;", "\u201d")):
        h = h.replace(a, b)
    lines = [re.sub(r"[ \t\u3000]+", " ", ln).strip() for ln in h.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def parse_detail(url, list_date):
    html = fetch(url)

    def meta(name):
        m = re.search(r'<meta name="%s" content="([^"]*)"' % name, html)
        return m.group(1).strip() if m else ""

    title = meta("ArticleTitle")
    pub = meta("PubDate")
    gen_date = pub.split(" ")[0] if pub else list_date
    column = meta("ColumnName") or "国家核安全局文件"

    # 正文: 取内容最长的容器(兼容新旧两套模板)
    text = ""
    for c in extract_containers(html):
        t = html_to_text(c)
        if len(t) > len(text):
            text = t

    # 附件: 正文里的 W0 下载链接(相对路径转绝对)
    atts, seen = [], set()
    base = url.rsplit("/", 1)[0] + "/"
    for c in extract_containers(html):
        for a in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', c, re.S):
            href = a.group(1).strip()
            if not (re.search(r"W0\d+", href)
                    or re.search(r"\.(pdf|doc|docx|wps|xls|xlsx|ceb)([?#]|$)", href, re.I)):
                continue
            if href.startswith("./"):
                href = base + href[2:]
            elif href.startswith("/"):
                href = "https://www.mee.gov.cn" + href
            elif not href.startswith("http"):
                href = base + href
            if href in seen:
                continue
            seen.add(href)
            name = html_to_text(a.group(2)) or href.rsplit("/", 1)[-1]
            atts.append({"名称": name, "链接": href})

    # 文号(本文自身的文号): 只认 1) 标题中 2) 独立成行的文号(可带"文号:"前缀)
    # 正文句内出现的〔20xx〕xx号 基本都是引用其他文件的文号, 不采信, 认不到就留空
    wholen = ""
    m = WHOLENO_RE.search(title)
    if m:
        wholen = re.sub(r"\s+", "", m.group(0))
    else:
        for ln in (text or "").split("\n"):
            ln = ln.strip()
            ln = re.sub(r"^(?:发文字号|文号|字号)\s*[:：]?\s*", "", ln)
            if WHOLENO_RE.fullmatch(ln):
                wholen = re.sub(r"\s+", "", ln)
                break

    # 成文日期: 正文中最后一次出现的"XXXX年X月X日"(落款)
    chengwen = ""
    last = None
    for mm in DATE_CN_RE.finditer(text):
        last = mm
    if last:
        try:
            chengwen = date(int(last.group(1)), int(last.group(2)), int(last.group(3))).isoformat()
        except ValueError:
            chengwen = ""

    return title, gen_date, column, text, atts, wholen, chengwen


def save_json(out, data):
    d = os.path.dirname(out)
    if d:
        os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, out)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def load_json(out):
    if not os.path.exists(out):
        return None
    with open(out, encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description="国家核安全局核安全文件栏目爬虫(增量)")
    ap.add_argument("--since", help="起始日期 YYYY-MM-DD(含)")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--delay", type=float, default=1.5)
    ap.add_argument("--max-pages", type=int, default=250)
    ap.add_argument("--limit", type=int, default=0, help="本次最多抓取详情篇数, 0=不限")
    ap.add_argument("--full-scan", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--remove-missing", action="store_true")
    ap.add_argument("--prune", action="store_true")
    args = ap.parse_args()

    data = load_json(args.out)
    if data is None:
        if not args.since:
            ap.error("目标 JSON 不存在, 首次运行必须用 --since 指定起始时间")
        data = {"meta": {"source": BASE_LIST.format("index.html"), "cutoff_date": args.since,
                         "last_run": None, "errors": {}},
                "documents": []}
    meta, docs = data.setdefault("meta", {}), data.setdefault("documents", [])
    by_id = {d["doc_id"]: d for d in docs if "doc_id" in d}

    cutoff = args.since or meta.get("cutoff_date")
    if args.since and args.since != meta.get("cutoff_date"):
        print(f"[i] cutoff 变更: {meta.get('cutoff_date')} -> {args.since}")
        meta["cutoff_date"] = args.since
    if not cutoff:
        ap.error("无法确定起始时间: JSON 无记录且未提供 --since")
    cutoff = str(cutoff)[:10]

    # 扫描边界: 增量模式扫到 max(cutoff, 上次运行日) 即可; --full-scan 从 cutoff 全窗扫
    boundary = cutoff
    if not args.full_scan and meta.get("last_run"):
        lr = str(meta["last_run"])[:10]
        if lr > boundary:
            boundary = lr

    print(f"[i] 目标: {args.out}")
    print(f"[i] 起始 cutoff={cutoff}, 扫描边界={boundary}"
          + ("(全窗校验)" if boundary == cutoff and args.full_scan else ""))
    print(f"[i] 已有文档 {len(by_id)} 篇, 开始扫描列表页 ...")

    # ---- 1. 扫描列表页, 收集窗口内条目 ----
    site_items = {}   # doc_id -> (date, url, list_title)
    scanned, stop = 0, False
    for idx in range(args.max_pages):
        page = "index.html" if idx == 0 else f"index_{idx}.html"
        try:
            html = fetch(BASE_LIST.format(page), delay=args.delay)
        except RuntimeError as e:
            print(f"[!] 列表页 {page} 抓取失败, 停止扫描: {e}")
            break
        items = parse_list(html)
        scanned += 1
        if not items:
            print(f"[i] {page} 无条目, 已到末页, 停止")
            break
        for d, url, t in items:
            if d >= cutoff:
                site_items[doc_id_of(url)] = (d, url, t)
        page_max = max(x[0] for x in items)
        print(f"    {page}: {len(items)} 条 (最新 {page_max}), 窗口内累计 {len(site_items)}")
        # 整页日期都早于边界 -> 后面只会更老, 停止
        if page_max < boundary:
            stop = True
        time.sleep(args.delay)
        if stop:
            break

    print(f"[i] 列表扫描完成: {scanned} 页, 窗口内官网条目 {len(site_items)} 篇")

    # ---- 2. 计算需要抓取的文档 ----
    now_iso = datetime.now().isoformat(timespec="seconds")
    to_fetch = []
    # 2a. 上次运行抓取失败的文档优先重试(不受扫描边界影响)
    for did, rec in list((meta.get("errors") or {}).items()):
        url = rec.get("url") if isinstance(rec, dict) else None
        if url and (did not in by_id or args.refresh):
            to_fetch.append((str(rec.get("date") or "") or "0000-00-00", did, url,
                             rec.get("list_title", "")))
    # 2b. 本轮列表扫描发现的新文档
    for did, (d, url, t) in site_items.items():
        if did not in by_id or args.refresh:
            to_fetch.append((d, did, url, t))
    to_fetch.sort(reverse=True)
    if args.limit and args.limit > 0:
        to_fetch = to_fetch[:args.limit]

    # ---- 3. 逐篇抓取详情 ----
    added = refreshed = failed = 0
    errors = meta.setdefault("errors", {})
    try:
        for n, (d, did, url, t) in enumerate(to_fetch, 1):
            try:
                title, gen, column, text, atts, wholen, chengwen = parse_detail(url, d)
                doc = {
                    "doc_id": did,
                    "名称": title or t,
                    "分类": column,
                    "发布机关": AGENCY,
                    "生成日期": gen,
                    "成文日期": chengwen,
                    "文号": wholen,
                    "正文": text,
                    "附件及链接": atts,
                    "链接": url,
                    "列表日期": d,
                    "crawled_at": now_iso,
                }
                doc.pop("site_status", None)
                if did in by_id:
                    docs[docs.index(by_id[did])] = doc
                    refreshed += 1
                else:
                    docs.append(doc)
                    added += 1
                by_id[did] = doc
                errors.pop(did, None)
                print(f"  ({n}/{len(to_fetch)}) + {gen} {doc['名称'][:40]}"
                      + (f" [附件x{len(atts)}]" if atts else ""))
            except RuntimeError as e:
                failed += 1
                errors[did] = {"url": url, "date": d, "list_title": t, "error": str(e)}
                print(f"  ({n}/{len(to_fetch)}) ! {did} 抓取失败: {e}")
            if n % 10 == 0:
                save_json(args.out, data)   # 中途落盘, 断点不丢
                print(f"  ... 中途已保存 ({len(docs)} 篇)")
            time.sleep(args.delay)
    except KeyboardInterrupt:
        print("\n[!] 手动中断, 保存当前进度 ...")

    # ---- 4. 一致性: 窗口内官网消失的文档打标记/删除 ----
    removed = marked = 0
    checked = 0
    for did, doc in list(by_id.items()):
        gen = str(doc.get("生成日期", ""))[:10]
        if gen < boundary or did in site_items:
            continue
        checked += 1
        if args.remove_missing:
            docs.remove(doc)
            by_id.pop(did)
            removed += 1
        else:
            if doc.get("site_status") != "missing_in_list":
                doc["site_status"] = "missing_in_list"
            marked += 1

    # ---- 5. cutoff 改晚时可选剪裁 ----
    pruned = 0
    if args.prune:
        for doc in list(docs):
            if str(doc.get("生成日期", ""))[:10] < cutoff:
                docs.remove(doc)
                by_id.pop(doc.get("doc_id"), None)
                pruned += 1

    # ---- 6. 排序 & 保存 ----
    docs.sort(key=lambda x: (str(x.get("生成日期", "")), x.get("doc_id", "")), reverse=True)
    meta["last_run"] = now_iso
    meta["last_run_summary"] = {
        "pages_scanned": scanned, "site_items_in_window": len(site_items),
        "added": added, "refreshed": refreshed, "failed": failed,
        "missing_marked": marked, "removed": removed, "pruned": pruned,
        "total_documents": len(docs),
    }
    if not errors:
        meta.pop("errors", None)
    save_json(args.out, data)

    print("\n========== 完成 ==========")
    print(f"列表页: {scanned} 页 | 窗口内官网条目: {len(site_items)}")
    print(f"新增: {added} | 重抓: {refreshed} | 失败: {failed}"
          + (f" (失败记录在 meta.errors, 下次运行自动重试)" if failed else ""))
    if marked or removed:
        print(f"官网未见(撤稿?): 标记 {marked} / 删除 {removed}")
    if pruned:
        print(f"按 cutoff 剪裁: {pruned}")
    print(f"JSON 现有文档总数: {len(docs)} -> {args.out}")


if __name__ == "__main__":
    main()
