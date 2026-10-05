# -*- coding: utf-8 -*-
"""从 data/pdf 样本裁剪出小体积测试夹具 -> tests/fixtures/（可重复执行）

夹具用途:
  license_design.pdf         证书页+附表（设计类, 无能力参数列）
  license_multi.pdf          多证合一（2 个证书块, 验证"一证一块"）
  change_scope.pdf           变更范围表（单页, 单证）
  change_multi.pdf           变更多单位（一页一家）
  change_design_params.pdf   变更范围表（设计能力特征参数列）
  overseas_single.pdf        境外注册登记确认书（单家, 表一/表二）
  overseas_multi.pdf         境外确认书（多单位, 序号条目）
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pypdf import PdfReader, PdfWriter  # noqa: E402

OUT = "tests/fixtures"
SPECS = [
    ("license_design.pdf",        "data/pdf/s1_颁发_设计单证_0.pdf", [1, 2, 3]),
    ("license_multi.pdf",         "data/pdf/s5_延续_18家合一_0.pdf", [1, 2, 3, 4, 5, 6, 7, 8]),
    ("change_scope.pdf",          "data/pdf/s3_变更_单证范围表_0.pdf", [1]),
    ("change_multi.pdf",          "data/pdf/s6_变更_20家范围表_0.pdf", [1, 2, 3]),
    ("change_design_params.pdf",  "data/pdf/s4_变更_双证_0.pdf", [1, 2]),
    ("overseas_single.pdf",       "data/pdf/s7_境外_单家_0.pdf", [1, 2]),
    ("overseas_multi.pdf",        "data/pdf/s8_境外_多单位名单_1.pdf", [1, 2, 3]),
]


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, src, pages in SPECS:
        if not os.path.exists(src):
            print(f"[!] 缺少源文件 {src}（先跑 scripts/m3_fetch_samples.py）")
            continue
        reader = PdfReader(src)
        writer = PdfWriter()
        for p in pages:
            writer.add_page(reader.pages[p - 1])
        dst = os.path.join(OUT, name)
        with open(dst, "wb") as f:
            writer.write(f)
        print(f"  {name:28s} {len(pages)} 页 {os.path.getsize(dst)/1024:.0f} KB  <- {os.path.basename(src)}")
    print(f"[i] 夹具目录: {OUT}")


if __name__ == "__main__":
    main()
