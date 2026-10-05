# -*- coding: utf-8 -*-
"""核安全局政策文件抽取验证 Demo：抓取列表页前2页 + 抽取1篇详情页"""
import re, json, time, urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")

def strip_tags(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s)).strip()

# ---- 1. 列表页抽取 ----
LIST_URL = "https://nnsa.mee.gov.cn/zcwj/haqjwj/wj/{}"
items = []
for page in ["index.html", "index_1.html"]:
    html = fetch(LIST_URL.format(page))
    for m in re.finditer(r'<a[^>]+href="(http[^"]+\.html)"[^>]*title="([^"]*)"', html):
        items.append({"title": m.group(2), "url": m.group(1)})
    time.sleep(1)
print(f"列表页共抽取条目: {len(items)}")

# ---- 2. 详情页抽取 ----
def parse_detail(url):
    html = fetch(url)
    d = {"url": url}
    d["title"] = (re.search(r'meta name="ArticleTitle" content="([^"]*)"', html) or [None, ""])[1]
    d["pub_date"] = (re.search(r'meta name="PubDate" content="([\d\- :]+)"', html) or [None, ""])[1].strip()
    d["column"] = (re.search(r'meta name="ColumnName" content="([^"]*)"', html) or [None, ""])[1]
    m = re.search(r'class="content_body_box">(.*?)</div>\s*<div', html, re.S)
    body_html = m.group(1) if m else ""
    d["body_text"] = strip_tags(body_html)[:300] + "..."  # demo 只截取
    # 附件（相对/绝对链接）
    atts = []
    for a in re.finditer(r'<a[^>]+href="([^"]*W0[^"]*\.(?:pdf|doc|docx|wps|xls|xlsx)[^"]*)"[^>]*>(?:\s*<[^>]*>)*([^<]*)', html):
        href = a.group(1)
        if href.startswith("./"):
            href = url.rsplit("/", 1)[0] + "/" + href[2:]
        elif href.startswith("/"):
            href = "https://www.mee.gov.cn" + href
        atts.append({"name": strip_tags(a.group(2)), "link": href})
    d["attachments"] = atts
    return d

detail = parse_detail(items[0]["url"])
result = {"sample_list_items": items[:6], "total_list_pages": 200, "detail_sample": detail}
with open("verify_sample.json", "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(json.dumps(result["detail_sample"], ensure_ascii=False, indent=2)[:1200])
