# -*- coding: utf-8 -*-
"""M3 探测二：下载典型样本 PDF 到 data/pdf/（分析用，不属于落地代码）

覆盖类型:
  1 颁发-单证(设计)  2 颁发-单证(安装)  3 变更-单证(范围表)
  4 变更-双证(设计+制造)  5 延续-多单位(18证合一PDF)
  6 变更-多单位(20家范围表)  7 境外注册登记(单+名单)  8 境外注册登记(多单位)
"""
import json, os, re, sys, time, urllib.request
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LABELED = "data/labeled_docs.json"
OUT = "data/pdf"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# 样本挑选: (文件名前缀, 标题包含关键字)
WANT = [
    ("s1_颁发_设计单证", "中核能源科技有限公司民用核安全设备设计许可证"),
    ("s2_颁发_安装单证", "中核华辰建筑工程有限公司民用核安全设备安装许可证"),
    ("s3_变更_单证范围表", "常州格林电力机械制造有限公司变更民用核安全设备制造"),
    ("s4_变更_双证", "中核苏阀科技实业股份有限公司变更民用核安全设备设计和制造"),
    ("s5_延续_18家合一", "中核苏阀科技实业股份有限公司等18家单位民用核安全设备许可证"),
    ("s6_变更_20家范围表", "中国第一重型机械股份公司等20家单位变更民用核安全设备制造"),
    ("s7_境外_单家", "美国Emerson Automation Solutions"),
    ("s8_境外_多单位名单", "韩国Doosan Enerbility"),
]


def main():
    data = json.load(open(LABELED, encoding="utf-8"))
    docs = [d for d in data["documents"] if d["labels"]]
    os.makedirs(OUT, exist_ok=True)
    manifest = []
    for prefix, key in WANT:
        hit = next((d for d in docs if key in d["名称"]), None)
        if not hit:
            print(f"[!] 未找到样本: {key}")
            continue
        print(f"\n【{prefix}】{hit['名称'][:60]}")
        print(f"    正文长度 {len(hit['正文'])} | 附件 {len(hit['附件及链接'])}")
        for i, a in enumerate(hit["附件及链接"]):
            url = a["链接"]
            ext = url.rsplit(".", 1)[-1].lower()
            if ext not in ("pdf", "doc", "docx"):
                print(f"    [跳过 {ext}] {a['名称'][:40]}")
                continue
            fn = f"{prefix}_{i}.{ext}"
            path = os.path.join(OUT, fn)
            if not os.path.exists(path):
                try:
                    req = urllib.request.Request(url, headers=UA)
                    with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as f:
                        f.write(r.read())
                    time.sleep(1.5)
                except Exception as e:  # noqa: BLE001
                    print(f"    [下载失败] {e}")
                    continue
            size = os.path.getsize(path)
            print(f"    -> {fn} ({size/1024:.0f} KB) {a['名称'][:44]}")
            manifest.append({
                "sample": prefix, "file": fn, "att_name": a["名称"], "url": url,
                "doc_id": hit["doc_id"], "doc_title": hit["名称"],
                "labels": hit["labels"], "doc_url": hit["链接"],
                "body": hit["正文"],
            })
    with open(os.path.join(OUT, "_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f"\n共下载 {len(manifest)} 个附件 -> {OUT}/_manifest.json")


if __name__ == "__main__":
    main()
