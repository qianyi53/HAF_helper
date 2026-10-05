# -*- coding: utf-8 -*-
"""定位解析异常页（开发调试用）：打印指定 PDF 指定页的文本与表格结构"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
import pdfplumber  # noqa: E402
from haf.pdf_parser import build_columns, is_header_row, page_text  # noqa: E402

path, pages = sys.argv[1], [int(x) for x in sys.argv[2].split(",")]
with pdfplumber.open(path) as pdf:
    print(f"共 {len(pdf.pages)} 页")
    for i in pages:
        page = pdf.pages[i - 1]
        print("=" * 90)
        print(f"--- p{i} 文本 ---")
        print(page_text(page)[:800])
        for ti, tb in enumerate(page.extract_tables(), 1):
            print(f"--- p{i} 表{ti}: {len(tb)}行 x {len(tb[0])}列; "
                  f"row0 is_header={is_header_row(tb[0])} "
                  f"row1 is_header={is_header_row(tb[1]) if len(tb) > 1 else 'NA'}")
            for j, c in enumerate(build_columns(tb)):
                print(f"    col{j}: field={c[0]} group={c[1]} param={c[2]}")
            for r in tb[:6]:
                print("    | " + " | ".join((c or "").replace("\n", "⏎")[:18] for c in r))
