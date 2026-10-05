# -*- coding: utf-8 -*-
"""M3 探测三：PDF 结构探测（页数/文本层/表格/一证一页）——分析用，不属于落地代码"""
import glob, json, os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import pdfplumber

MANIFEST = json.load(open("data/pdf/_manifest.json", encoding="utf-8"))
BY_FILE = {m["file"]: m for m in MANIFEST}

for path in sorted(glob.glob("data/pdf/*.pdf")):
    fn = os.path.basename(path)
    meta = BY_FILE.get(fn, {})
    print("=" * 100)
    print(f"【{fn}】{meta.get('att_name', '')[:60]}")
    try:
        with pdfplumber.open(path) as pdf:
            npages = len(pdf.pages)
            print(f"  页数: {npages}")
            # 逐页: 文本长度 / 表格数 / 首行
            for i, page in enumerate(pdf.pages):
                txt = page.extract_text() or ""
                tables = page.find_tables()
                first = txt.strip().split("\n")[0][:70] if txt.strip() else "(无文本层)"
                tb_info = ""
                if tables:
                    t = tables[0]
                    tb_info = f" | 表{len(tables)}个 首表{len(t.rows)}行x{len(t.columns)}列"
                print(f"    p{i+1}: 文本{len(txt):5d}字 | 首行: {first}{tb_info}")
    except Exception as e:  # noqa: BLE001
        print(f"  [解析失败] {e}")
