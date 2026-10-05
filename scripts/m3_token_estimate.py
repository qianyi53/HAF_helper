# -*- coding: utf-8 -*-
"""M3 探测十一：2021 以来全量解析的输入/输出 token 实测评估（分析用，不属落地代码）

方法:
  1. 对 10 个样本 PDF 逐页实测 (tiktoken o200k_base)：文本 token、表格(markdown化) token
  2. 按块（一证一块 / 一页一家）聚合，实测块级输入规模
  3. 按建议 schema 构造"块级输出 JSON"，实测输出规模（固定字段 + 每行范围明细）
  4. 结合全量估算（820 块 / 2224 页）外推三种模式的总量
"""
import glob
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import pdfplumber
import tiktoken

ENC = tiktoken.get_encoding("o200k_base")
tok = lambda s: len(ENC.encode(s))

# ---------- 1. 提示词模板（按方案 P0 的实际形态写一段，实测其 token） ----------
PROMPT_TPL = """你是核安全许可文书的字段抽取器。输入是「一个」许可块（含块头、页文本、表格 markdown）。
只做抽取与归一，不推测、不补全；缺失填 null；数值保留原文含义（如"≤5030（事故工况下≤8675）"→ {"值":5030,"单位":"kN","上限":true,"事故工况":{"值":8675}}）。
输出严格 JSON，字段：
{license:{证字号,单位名称,法定代表人,单位住所,活动种类[],证件类型,有效期原文,有效期起,有效期止,
 设备类别汇总[],核安全级别汇总,docs:{doc_id,官网链接,附件url,页码起,页码止}},
 scope_items:[{设备类别,设备品种,核安全级别,能力特征参数{},主要关键工艺[],活动范围及完成形式,活动场所,备注,分包项目[]}]}
规则：块头里的 doc_id/链接/页码必须原样回填；同一块内多张表合并到一个 scope_items 数组；不要输出解释。
"""
P_TOK = tok(PROMPT_TPL)

# 块头（上下文粘合）模板
HEADER_TPL = """【文档】{title} | doc_id={did} | {url}
【附件】{att} | {atturl} | 共{n}页
【块】第 k/N 块 | 页 {p1}-{p2} | 类型={bt} | 块内证字号={cert}
"""

# ---------- 2. 输出 JSON 的实测模板（用真实字段名 + 真实取值片段） ----------
OUT_FIXED = json.dumps({
    "license": {"证字号": "国核安证字S（26）16号", "单位名称": "中核能源科技有限公司",
                "法定代表人": "王志刚", "单位住所": "北京市海淀区东北旺西路8号中关村软件园26号楼",
                "活动种类": ["设计"], "证件类型": "民用核安全设备设计许可证",
                "有效期原文": "许可证有效期5年。", "有效期起": "2026-09-01", "有效期止": "2031-08-31",
                "设备类别汇总": ["压力容器", "管道和管配件", "支承件", "法兰", "阀门", "风机"],
                "核安全级别汇总": ["核安全2、3级", "核安全级", "1E级"],
                "docs": {"doc_id": "t20260922_1170491", "官网链接": "https://www.mee.gov.cn/x", "附件url": "https://www.mee.gov.cn/y.pdf",
                         "页码起": 3, "页码止": 5}}},
    ensure_ascii=False)
OUT_ROW = json.dumps({
    "设备类别": "阀门", "设备品种": "隔离阀", "核安全级别": "2级",
    "能力特征参数": {"公称通径": {"值": 1050, "单位": "DN", "上限": True},
                     "设计压力": {"值": 8.9, "单位": "MPa", "上限": True},
                     "设计温度": {"值": 320, "单位": "℃", "上限": True}},
    "主要关键工艺": ["装配", "焊接", "功能性试验"],
    "活动范围及完成形式": "以技术规格书为依据，完成设备的全部施工图设计",
    "活动场所": "江苏省苏州高新区浒墅关镇安杨路178号",
    "备注": "1.若压力等级（磅级）在原许可活动范围内，则可覆盖相应规格；2.分包项目：射线检验、热处理。",
}, ensure_ascii=False)
OUT_REASON_TOK = tok('{"match_reason":"…"}\n')
FIXED_TOK = tok(OUT_FIXED) + OUT_REASON_TOK
ROW_TOK = tok(OUT_ROW)
print(f"[模板实测] 提示词 {P_TOK} tok | 输出固定部分 {FIXED_TOK} tok | 每行范围明细 {ROW_TOK} tok\n")

# ---------- 3. 逐文件实测：页文本/表格 token、按块聚合 ----------
def page_payload(page):
    """按方案给 LLM 的页面载荷: 文本 + 表格 markdown"""
    txt = page.extract_text() or ""
    tb_md = []
    for tb in page.extract_tables():
        for r in tb:
            tb_md.append("| " + " | ".join((c or "").replace("\n", " ") for c in r) + " |")
    return txt + ("\n[表格]\n" + "\n".join(tb_md) if tb_md else "")

CERT_RE = re.compile(r"^民用核安全设备(设计|制造|安装)许可证")
UNIT_TABLE_RE = re.compile(r"^.{4,45}(民用核(电)?安全设备).{0,12}(许可活动范围表|许可证)")
OVERSEA_RE = re.compile(r"^(一|二|三|四|五|六|七|八|九|十|十一|十二)、")

per_file = []
all_rows = 0
all_pages = 0
for path in sorted(glob.glob("data/pdf/*.pdf")):
    with pdfplumber.open(path) as pdf:
        pages = [page_payload(p) for p in pdf.pages]
        firsts = []
        for p in pdf.pages:
            t = (p.extract_text() or "").strip().split("\n")
            firsts.append([x.strip() for x in t[:2]])
        # 块切分（按方案锚点，证书页优先；无证书页时按单位范围表页/境外条目页）
        cert_starts = [i for i, f in enumerate(firsts, 1)
                       if CERT_RE.match(f[0]) or (f[0] == "附件" and len(f) > 1 and CERT_RE.match(f[1]))]
        oversea_starts = [i for i, f in enumerate(firsts, 1) if OVERSEA_RE.match(f[0])]
        unit_starts = [i for i, f in enumerate(firsts, 1) if UNIT_TABLE_RE.match(f[0])]
        if cert_starts:
            starts = cert_starts
        elif oversea_starts:
            starts = ([1] if oversea_starts[0] > 1 else []) + oversea_starts
        elif unit_starts:
            starts = unit_starts
        else:
            starts = [1]
        blocks = []
        for k, s in enumerate(starts):
            e = (starts[k + 1] - 1) if k + 1 < len(starts) else len(pdf.pages)
            blocks.append((s, e))
        # 表格行数（= 输出 scope_items 行数量级）；扣掉 1~2 行表头
        rows = 0
        for p in pdf.pages:
            for tb in p.extract_tables():
                rows += max(0, len(tb) - 2)
        per_file.append({"file": os.path.basename(path), "pages": len(pdf.pages),
                         "blocks": len(blocks), "rows": rows,
                         "block_tok": [tok("\n".join(pages[s - 1:e])) for s, e in blocks],
                         "page_tok": [tok(x) for x in pages]})
        all_rows += rows
        all_pages += len(pdf.pages)

print(f"[样本实测] 文件 {len(per_file)} | 页 {all_pages} | 块 {sum(f['blocks'] for f in per_file)} | 表行 {all_rows}")
for f in per_file:
    bt = f["block_tok"]
    print(f"  {f['file'][:30]:32s} 页{f['pages']:3d} 块{f['blocks']:3d} 行{f['rows']:4d} "
          f"块token均值 {sum(bt)/len(bt):7.0f} (max {max(bt)})")

avg_page_tok = sum(sum(f["page_tok"]) for f in per_file) / all_pages
avg_block_tok = sum(sum(f["block_tok"]) for f in per_file) / sum(f["blocks"] for f in per_file)
rows_per_block = all_rows / sum(f["blocks"] for f in per_file)
print(f"\n[实测均值] 页 {avg_page_tok:.0f} tok | 块(纯正文) {avg_block_tok:.0f} tok | 行/块 {rows_per_block:.1f}")

# ---------- 4. 全量外推（820 块 / 2224 页，来自 m3_block_estimate.py） ----------
BLOCKS, PAGES = 820, 2224
hdr = tok(HEADER_TPL.format(title="关于批准延续××等18家单位民用核安全设备许可证的通知",
                            did="t20260922_1170491", url="https://www.mee.gov.cn/xxgk2018/xxgk/xxgk09/202609/t20260922_1170491.html",
                            att="各单位民用核安全设备许可证", atturl="https://www.mee.gov.cn/xxgk2018/xxgk/xxgk09/202609/W020260922515476036999.pdf",
                            n=83, p1=3, p2=5, bt="许可证块", cert="国核安证字S（26）17号"))
block_in = avg_block_tok + hdr + P_TOK
row_out = ROW_TOK + tok('{"source_page": 4, "block_id": "t20260922_1170491#b1"}')
block_out = FIXED_TOK + rows_per_block * row_out
print(f"[单块] 输入 {block_in:.0f} tok (正文{avg_block_tok:.0f}+块头{hdr}+提示词{P_TOK}) | 输出 {block_out:.0f} tok")

print("\n### 全量解析 token 评估（820 块 / 2224 页 / 约 %d 行范围明细）" % round(rows_per_block * BLOCKS))
scen = [
    ("S1 全量 LLM 抽取（所有块过 LLM）", 1.0, BLOCKS),
    ("S2 规则优先 + 30% 块 LLM 兜底", 0.3, BLOCKS),
    ("S3 规则优先 + 10% 块 LLM 兜底", 0.1, BLOCKS),
]
for name, ratio, n in scen:
    i = block_in * n * ratio
    o = block_out * n * ratio
    print(f"  {name:34s} 输入 {i/1e6:5.2f}M tok | 输出 {o/1e6:5.2f}M tok | 合计 {(i+o)/1e6:5.2f}M")

# 变更摘要（P1）+ 重试开销
chg_tok_in = tok(json.dumps({"before": ["…"], "after": ["…"]}, ensure_ascii=False)) * 8
print(f"\n  变更 diff 摘要(P1) 约 101 篇 × <1k tok ≈ {101*0.8:.0f}k tok（可忽略量级）")
for name, ratio, n in scen:
    i, o = block_in * n * ratio, block_out * n * ratio
    print(f"  {name:34s} 含 1.3× 重试/复检开销: 输入 {i*1.3/1e6:5.2f}M | 输出 {o*1.3/1e6:5.2f}M")
