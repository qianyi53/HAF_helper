# -*- coding: utf-8 -*-
"""haf/pdf_parser.py — L3 PDF 解析：页级预分块 + 确定性抽取 + LLM 兜底

设计依据: docs/reports/2026-09-27-【分析】-M3-PDF解析特征分析报告.md §4/§6
  ① 分块: 页级锚点（证书页 / 单位范围表页 / 境外条目页）→ Block，块头注入防串证
  ② 确定性抽取: pdfplumber 表格 + 正则（证字号/单位/有效期/范围表行）
  ③ LLM 兜底: 仅当规则拿不到关键字段或表格行时，调用单块 LLM 归一（P0）

用法（库）:
  from haf.pdf_parser import parse_pdf
  records = parse_pdf("data/pdf/xxx.pdf", ctx={...}, llm=None)
命令行:
  python -X utf8 -m haf.pdf_parser --pdf data/pdf/s1_颁发_设计单证_0.pdf [-v]
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

import pdfplumber

from haf import prompts

# ---------------- 锚点 ----------------
CERT_TITLE_RE = re.compile(r"民用核安全设备\s*(设计|制造|安装)\s*许可证")
CERT_NO_RE = re.compile(r"国核安证字\s*([SZA])?\s*[（(]\s*(\d{2,4})\s*[）)]\s*(\d+)\s*号")
UNIT_TABLE_RE = re.compile(r"^.{4,45}(民用核核电?安全设备|民用核安全设备).{0,12}(许可活动范围表|许可证)")
OVERSEA_ITEM_RE = re.compile(r"^(一|二|三|四|五|六|七|八|九|十|十一|十二|十三|十四|十五|十六|十七|十八|十九|二十)、")
OVERSEA_CERT_RE = re.compile(r"单位\s*注册登记确认书|境外单位注册登记确认书")
FU_TABLE_RE = re.compile(r"^(附表|表[一二])")
CN_DATE_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")

CERT_KIND_ENUM = ("设计", "制造", "安装", "境外注册登记")

# 证书页键值字段（值可能跨行，遇下一个键或空行结束）
CERT_KEYS = ("单位名称", "法定代表人", "单位住所", "设备类别", "核安全级别",
             "单位所在国", "活动类别", "设备品种")

# 表头 → 规范字段（顺序即优先级）
HEADER_MAP = [
    (r"设备种类", "设备种类"),
    (r"设备类别", "设备类别"),
    (r"设备品种/设备名称", "设备品种"),
    (r"设备品种", "设备品种"),
    (r"设备名称", "设备品种"),
    (r"民用核设施类别", "民用核设施类别"),
    (r"核设施类别", "民用核设施类别"),
    (r"核安全级别", "核安全级别"),
    (r"主要关键工艺", "主要关键工艺"),
    (r".*活动范围及.*完成形式", "活动范围及完成形式"),
    (r"活动场所", "活动场所"),
    (r"备注", "备注"),
]
PARAM_GROUPS = ("设计能力特征参数", "制造能力特征参数")
# 能力特征参数/设备参数列 → (单位, 类型)；未在 PARAM_GROUPS 下的独立参数列也在此登记
PARAM_META = {
    "公称通径": ("DN", "num"),
    "设计压力": ("MPa", "num"),
    "设计温度": ("℃", "num"),
    "载荷等级": ("kN", "num"),
    "结构形式": ("", "text"),
    "典型设备名称": ("", "list"),
    "材料类别": ("", "list"),      # 铸锻件类常见
    "成品重量": ("t", "num"),      # 铸锻件类常见
    "堆型功率": ("", "text"),      # 原列名"堆型、功率"
    "壁厚": ("mm", "num"),         # 储罐/换热器类
    "管板厚度": ("mm", "num"),     # 换热器类
}


def param_key(s):
    """参数列名归一：去空白/括号注释/顿号 → 与 PARAM_META 键一致"""
    s = re.sub(r"[（(][^）)]*[）)]", "", norm(s))
    return re.sub(r"[、，,;；]", "", s)
# 需要"向下填充"的列：PDF 中合并单元格会让后续行该列为空，语义上表示"同上"
FILLDOWN_FIELDS = ("设备种类", "设备类别", "设备品种", "民用核设施类别", "核安全级别",
                   "活动范围及完成形式", "活动场所", "主要关键工艺", "备注")

ACTIVITY_FROM_CERT_KIND = {"设计": ["设计"], "制造": ["制造"], "安装": ["安装"],
                           "境外注册登记": ["设计", "制造"]}


# ================= 通用工具 =================
def norm(s):
    """压空白（含全角空格与换行）"""
    return re.sub(r"[\s\u3000]+", "", s or "")


def split_multi(s):
    """多值拆分：顿号/逗号/分号/换行 → 列表（保序去重）"""
    if not s:
        return []
    parts = [p.strip() for p in re.split(r"[、,，;；\n]", s)]
    out = []
    for p in parts:
        if p and p not in out:
            out.append(p)
    return out


def clean_cell(s):
    """单元格清洗：压掉 PDF 换行造成的空格；以拉丁文为主（如英文单位名/地址）时保留单词间单空格"""
    if not s:
        return s
    s = re.sub(r"[\s\u3000]+", " ", s).strip()
    latin = len(re.findall(r"[A-Za-z]", s))
    cjk = len(re.findall(r"[\u4e00-\u9fa5]", s))
    if latin > cjk * 0.3:          # 拉丁为主 → 保留空格（Emerson Automation Solutions LP）
        return s
    return re.sub(r"[\s\u3000]+", "", s)


def parse_value(s, unit=""):
    """能力参数值解析: "≤1050" / "≤5030（事故工况下≤8675）" / "常温" / "/" """
    s = (s or "").strip()
    if not s or s in ("/", "-", "—", "无"):
        return None
    m = re.match(r"^[≤<>=约]*\s*(\d+(?:\.\d+)?)\s*(.*)$", s)
    if m:
        num = float(m.group(1))
        num = int(num) if num == int(num) else num
        extra = m.group(2).strip()
        val = {"值": num}
        if "≤" in s:
            val["上限"] = True
        if extra and not extra.startswith("（") and not extra.startswith("("):
            val["单位"] = extra
        # 附加工况: （事故工况下≤8675）
        add = re.search(r"[（(]\s*([^）)]*?)[）)]", s)
        if add:
            inner = add.group(1)
            for sub in re.split(r"[；;]", inner):
                key = re.sub(r"^.*?([\u4e00-\u9fa5]{2,6}工况).*$", r"\1", sub)
                mv = re.search(r"[≤<]?\s*(\d+(?:\.\d+)?)", sub)
                if mv and "工况" in sub:
                    val.setdefault("附加", {})[key] = {"值": float(mv.group(1)),
                                                     "上限": "≤" in sub}
        if unit and "单位" not in val:
            val["单位"] = unit
        return val
    return {"原文": s}


def parse_cert_no(text):
    m = CERT_NO_RE.search(norm(text) if "\n" not in text else text)
    if not m:
        return None
    kind, year, seq = m.group(1) or "", m.group(2), m.group(3)
    return f"国核安证字{kind}（{year}）{int(seq):02d}号" if kind else f"国核安证字（{year}）{int(seq):02d}号"


def parse_cn_date(text):
    m = CN_DATE_RE.search(text or "")
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


# ================= PDF 读取 =================
def page_text(page):
    return page.extract_text() or ""


def page_tables_md(page):
    out = []
    for tb in page.extract_tables():
        for r in tb:
            out.append("| " + " | ".join((c or "").replace("\n", " ") for c in r) + " |")
    return "\n".join(out)


def page_payload(page):
    txt = page_text(page)
    md = page_tables_md(page)
    return txt + ("\n[表格]\n" + md if md else "")


def head_lines(txt, n=2):
    lines = [x.strip() for x in (txt or "").strip().split("\n") if x.strip()]
    return lines[:n]


# ================= 分块 =================
class Block:
    def __init__(self, kind, p1, p2, cert_kind=None, unit_name=None):
        self.kind = kind              # license | scope_only | overseas | other
        self.p1, self.p2 = p1, p2
        self.cert_kind = cert_kind
        self.unit_name = unit_name

    def pages(self):
        return range(self.p1, self.p2 + 1)

    def __repr__(self):
        return f"<Block {self.kind} p{self.p1}-{self.p2} {self.cert_kind or ''}>"


PREFIX_RE = re.compile(r"^附件\s*\d*\s*$")


def anchor_kind_of_head(lines):
    """页首若干行的锚点判定（PDF 首页常为"附件 / 各单位… / 真正标题"多层前缀）

    返回 'cert' | 'unit' | 'oversea' | None
    """
    body = [ln for ln in lines[:4] if ln and not PREFIX_RE.match(ln)]
    for ln in body[:3]:
        if CERT_TITLE_RE.search(ln):
            return "cert"
    for ln in body[:2]:
        if OVERSEA_ITEM_RE.match(ln):
            return "oversea"
    if any(OVERSEA_CERT_RE.search(ln) for ln in body[:3]):
        return "oversea"
    for ln in body[:2]:
        if UNIT_TABLE_RE.match(ln):
            return "unit"
    return None


def split_blocks(pdf):
    """页级锚点切块（M3 报告 §6.2）:
    证书首页优先（一证一块）；无证书页时按"单位范围表页"或"境外条目页"切块。
    """
    heads = [head_lines(page_text(p), 4) for p in pdf.pages]
    cert_pages, oversea_pages, unit_pages = [], [], []
    for i, h in enumerate(heads, 1):
        kind = anchor_kind_of_head(h)
        if kind == "cert":
            cert_pages.append(i)
        elif kind == "oversea":
            oversea_pages.append(i)
        elif kind == "unit":
            unit_pages.append(i)
    # 同一单位的连续页（范围表跨页/续表）不产生新块
    unit_pages = dedupe_by_unit(pdf, unit_pages)

    blocks = []
    if cert_pages:
        for k, s in enumerate(cert_pages):
            e = (cert_pages[k + 1] - 1) if k + 1 < len(cert_pages) else len(pdf.pages)
            title_src = page_text(pdf.pages[s - 1]) + (hint_head(pdf, s))
            m = CERT_TITLE_RE.search(title_src)
            blocks.append(Block("license", s, e, m.group(1) if m else None))
    elif oversea_pages:
        starts = ([1] if oversea_pages[0] > 1 else []) + oversea_pages
        for k, s in enumerate(starts):
            e = (starts[k + 1] - 1) if k + 1 < len(starts) else len(pdf.pages)
            blocks.append(Block("overseas", s, e))
    elif unit_pages:
        for k, s in enumerate(unit_pages):
            e = (unit_pages[k + 1] - 1) if k + 1 < len(unit_pages) else len(pdf.pages)
            title = " ".join(head_lines(page_text(pdf.pages[s - 1]), 3))
            blocks.append(Block("scope_only", s, e,
                                cert_kind=cert_kind_of_title(title),
                                unit_name=unit_name_of_title(title)))
    else:
        blocks.append(Block("other", 1, len(pdf.pages)))
    return blocks


def dedupe_by_unit(pdf, pages):
    """连续多页同属一个单位（范围表跨页）时只保留首页为新块起点"""
    out, last_unit = [], None
    for p in pages:
        title = " ".join(head_lines(page_text(pdf.pages[p - 1]), 4))
        unit = unit_name_of_title(title)
        if unit and unit == last_unit:
            continue
        out.append(p)
        last_unit = unit
    return out


def hint_head(pdf, page_no):
    """证书标题可能在页首第 1 行，也可能在第 2 行（首行为"附件"）"""
    txt = page_text(pdf.pages[page_no - 1])
    return "\n" + "\n".join(head_lines(txt, 2))


CERT_KIND_OF_TITLE_RE = re.compile(r"民用核(电)?安全设备\s*(设计|制造|安装)")


def unit_name_of_title(title):
    """从"××公司民用核安全设备制造许可活动范围表"标题提取单位名"""
    m = re.match(r"^(.{4,45}?)(民用核核电?安全设备|民用核安全设备)", title or "")
    return remove_attachment_prefix(m.group(1)) if m else None


def cert_kind_of_title(title):
    m = CERT_KIND_OF_TITLE_RE.search(title or "")
    return m.group(2) if m else None


def remove_attachment_prefix(s):
    """去掉标题里可能带的"附件 1 "等前缀"""
    return re.sub(r"^(附件\s*\d*\s*)+", "", s or "").strip()


# ================= 表格 → 范围明细 =================
def header_block_len(tb, cap=10):
    """表头行数：从第 0 行起连续的「所有非空单元格都是短文本(≤12 字)」的行（表头可能碎片化成多行）。
    校验：合并后的表头必须含至少一个已知表头/参数名，否则退回 1 行。
    """
    n = 1
    for i, row in enumerate(tb[:cap]):
        cells = [norm(c) for c in row if norm(c)]
        if not cells:
            continue          # 表头内的空行（PDF 行拆分产生），不终止表头区
        if any(len(c) > 12 for c in cells):
            break
        n = i + 1
    merged = merge_header_rows(tb[:n])
    if not any(is_header_cell(m) for m in merged):
        return 1
    return n


def merge_header_rows(rows):
    """把多行表头按列纵向拼接（处理 PDF 把"核设施/类别"拆到两行的情况）"""
    if not rows:
        return []
    ncol = max(len(r) for r in rows)
    out = []
    for j in range(ncol):
        parts = [norm(r[j]) for r in rows if j < len(r) and norm(r[j])]
        out.append("".join(parts))
    return out


def build_columns(tb):
    """多行表头合并 → 列定义 [(字段, 参数组, 参数名)]；能力特征参数组内未识别列并入该组"""
    if not tb:
        return []
    n = header_block_len(tb)
    merged = merge_header_rows(tb[:n])
    cols = []
    for text in merged:
        field = group = pname = None
        if text:
            # 先认参数列（"典型设备名称"等与字段名存在词面重叠，须优先）
            if param_key(text) in PARAM_META:
                group, pname = "能力特征参数", text
            if not group:
                for g in PARAM_GROUPS:
                    if g in text:
                        group = g
                        rest = text.split(g, 1)[1]
                        pname = rest if rest else None
                        break
            if not group:
                for pat, name in HEADER_MAP:
                    if re.search(pat, text):
                        field = name
                        break
        cols.append([field, group, pname, text])
    # 组跨度：特征参数组起始列之后的「未识别且非字段」列，并入该参数组
    cur_group = None
    for c in cols:
        field, group, pname, text = c
        if field:
            cur_group = None
        elif group and pname:
            cur_group = group
        elif group:
            cur_group = group
        elif text and cur_group:
            c[1], c[2] = cur_group, text
    return [(c[0], c[1], c[2]) for c in cols]


def is_header_cell(cell):
    """单格是否为表头格：短文本且完整等于表头名（避免把"活动场所仅限于…"这类数据误判）"""
    s = norm(cell)
    if not s or len(s) > 14:
        return False
    if any(re.fullmatch(pat, s) for pat, _ in HEADER_MAP):
        return True
    if "特征参数" in s:
        return True
    return param_key(s) in PARAM_META


def is_header_row(row):
    """表头行判定：表头格 ≥ 2（单格表头行如"附表/备注"另由数据行判定兜住）"""
    return sum(1 for c in row if is_header_cell(c)) >= 2


def is_data_row(row):
    return any((c or "").strip() for c in row) and not is_header_row(row)


def table_rows(page):
    """页内所有表格的数据行（含表头行）"""
    return [tb for tb in page.extract_tables() if tb]


def page_activities(page):
    """境外确认书页内的"表一 设计活动注册登记范围 / 表二 制造活动注册登记范围"标记顺序"""
    txt = page_text(page)
    out = []
    for m in re.finditer(r"表[一二][^\n]{0,20}", txt):
        seg = m.group(0)
        if "设计" in seg:
            out.append("设计")
        elif "制造" in seg:
            out.append("制造")
    return out


def extract_scope_items(page, page_no, activities=None):
    """把一页中的表格解析成范围明细行（字段 + 能力特征参数 JSON）

    activities: 与本页表格按顺序对应的活动类别（境外确认书 表一设计/表二制造）
    """
    items = []
    page_acts = activities if activities is not None else page_activities(page)
    for ti, tb in enumerate(table_rows(page)):
        if not tb:
            continue
        activity = page_acts[ti] if ti < len(page_acts) else None
        hdr_len = header_block_len(tb)
        cols = build_columns(tb)
        carry = {}
        for row in tb[hdr_len:]:
            if not any((c or "").strip() for c in row) or is_header_row(row):
                continue
            item, params = {}, {}
            for j, cell in enumerate(row):
                if j >= len(cols):
                    break
                field, group, pname = cols[j]
                val = clean_cell(cell)
                if not val:
                    continue
                if field:
                    item[field] = val
                elif group:
                    key = pname or "值"
                    params[key] = val
            if not item and not params:
                continue
            # 合并单元格向下填充（同一设备类别的后续行继承）
            merged = dict(carry)
            merged.update({k: v for k, v in item.items() if k not in FILLDOWN_FIELDS or v})
            for k in FILLDOWN_FIELDS:
                if not merged.get(k) and carry.get(k):
                    merged[k] = carry[k]
            for k in FILLDOWN_FIELDS:
                if merged.get(k):
                    carry[k] = merged[k]
            # 能力特征参数 → JSON
            cap = {}
            for k, v in params.items():
                pname_norm = param_key(k)
                unit, kind = PARAM_META.get(pname_norm, ("", "num"))
                pv = parse_value(v, unit)
                if kind == "list" and pv and "原文" in pv:
                    pv = split_multi(pv["原文"])
                if pv is not None:
                    cap[pname_norm] = pv
            scope = {
                "设备种类": merged.get("设备种类"),
                "设备类别": merged.get("设备类别"),
                "设备品种": merged.get("设备品种"),
                "民用核设施类别": merged.get("民用核设施类别"),
                "活动类别": activity,
                "核安全级别": merged.get("核安全级别"),
                "能力特征参数": cap,
                "主要关键工艺": split_multi(merged.get("主要关键工艺")),
                "活动范围及完成形式": merged.get("活动范围及完成形式"),
                "活动场所": merged.get("活动场所"),
                "备注": merged.get("备注"),
                "分包项目": extract_subcontract(merged.get("备注")),
                "源页": page_no,
            }
            if any(scope[k] for k in ("设备类别", "设备品种", "民用核设施类别")) or scope["能力特征参数"]:
                items.append(scope)
    return items


def extract_subcontract(remark):
    """备注里的"主要分包项目：1.…；2.…" → 列表（保留数值开头，如"80吨以上…"）"""
    if not remark or "分包" not in remark:
        return []
    part = re.split(r"分包项目\s*[:：]", remark, maxsplit=1)
    if len(part) < 2:
        return []
    items = []
    for x in re.split(r"[；;]", part[1]):
        x = re.sub(r"^\s*\d+\s*[.、)）]\s*", "", x.strip()).strip("。；; ")
        if x and len(x) > 1:
            items.append(x)
    return items


# ================= 证书页字段 =================
# 值续行终止模式：遇到正文句子/落款/页码即停止吸收（避免把正文并入字段值）
VALUE_STOP_RE = re.compile(
    r"^(国家核安全局|我局|你(公司|单位|院)|根据《|决定批准|—|附件|此件|抄送|主题词|"
    r"民用核安全设备.{0,6}许可证$|中华人民共和国民用核安全设备|注册登记条件|表[一二])")


def _key_pattern(key):
    """键名允许字符间有空格（PDF 常见"单 位 名 称：…"）"""
    return re.compile("^" + r"\s*".join(re.escape(c) for c in key) + r"\s*[:：]\s*(.*)$")


CERT_KEY_PATTERNS = [(_key_pattern(k), k) for k in CERT_KEYS]


def extract_cert_fields(text):
    """证书页键值字段（值可跨行；遇到下一个键 / 正文句 / 空行结束）"""
    lines = [ln.strip() for ln in (text or "").split("\n")]
    out = {}
    cur_key = None
    for ln in lines:
        if not ln:
            cur_key = None
            continue
        hit = None
        for pat, k in CERT_KEY_PATTERNS:
            m = pat.match(ln)
            if m:
                hit = (k, m.group(1))
                break
        if hit:
            cur_key = hit[0]
            out[cur_key] = hit[1].strip()
        elif cur_key and not VALUE_STOP_RE.match(ln):
            out[cur_key] = (out[cur_key] + ln).strip()
        else:
            cur_key = None
    return {k: clean_cell(v) for k, v in out.items()}


def extract_validity(text, issue_date=None):
    """有效期：优先"有效期至 YYYY年M月D日"，其次"有效期 N 年" + 发证日期推算"""
    if not text:
        return ("", None)
    m = re.search(r"有效期\s*至\s*([^\n。；]{4,20})", text)
    if m:
        d = parse_cn_date(m.group(1))
        return (f"许可证有效期至{m.group(1).strip()}", d)
    m = re.search(r"有效期\s*(\d+)\s*年", text)
    if m:
        years = int(m.group(1))
        if not issue_date:
            return (f"许可证有效期{years}年", None)
        d0 = dt.date.fromisoformat(str(issue_date)[:10])
        try:
            end = d0.replace(year=d0.year + years) - dt.timedelta(days=1)
        except ValueError:
            end = d0.replace(year=d0.year + years, day=28) - dt.timedelta(days=1)
        return (f"许可证有效期{years}年（自{issue_date}推算）", end.isoformat())
    return ("", None)


# ================= 单块解析 =================
def parse_license_block(pdf, block, ctx):
    pages = list(block.pages())
    head_txt = page_text(pdf.pages[pages[0] - 1])
    title_page_txt = "\n".join(page_text(pdf.pages[p - 1]) for p in pages[:2])
    fields = extract_cert_fields(title_page_txt)
    cert_no = parse_cert_no(title_page_txt)
    # 事件：取证/首次 → 颁发；延续 → 延续
    event = None
    if re.search(r"(取证|首次|颁发|新申请)", title_page_txt):
        event = "颁发"
    if re.search(r"延续申请", title_page_txt):
        event = "延续"
    # 有效期在条件页（第 2 页起）
    cond_txt = "\n".join(page_text(pdf.pages[p - 1]) for p in pages[1:])
    issue_date = ctx.get("成文日期") or ctx.get("doc_date")
    valid_txt, valid_end = extract_validity(cond_txt or head_txt, issue_date)

    scope = []
    for p in pages:
        scope += extract_scope_items(pdf.pages[p - 1], p)

    nonlocal_acts = ACTIVITY_FROM_CERT_KIND.get(block.cert_kind or "", [])
    rec = {
        "kind": "license",
        "cert_kind": block.cert_kind,
        "cert_no": cert_no,
        "unit_name": clean_cell(fields.get("单位名称")) or None,
        "event": event,
        "活动种类": ACTIVITY_FROM_CERT_KIND.get(block.cert_kind or "", []),
        "fields": {
            "法定代表人": clean_cell(fields.get("法定代表人")) or None,
            "单位住所": clean_cell(fields.get("单位住所")) or None,
            "设备类别": split_multi(fields.get("设备类别")),
            "核安全级别": split_multi(fields.get("核安全级别")),
            "有效期原文": valid_txt or None,
            "有效期止": valid_end,
            "发证日期": issue_date,
        },
        "scope_items": scope,
        "page_start": block.p1,
        "page_end": block.p2,
        "extract_mode": "rule",
    }
    return rec


def parse_scope_only_block(pdf, block, ctx):
    scope = []
    for p in block.pages():
        scope += extract_scope_items(pdf.pages[p - 1], p)
    att = ctx.get("att_name") or ""
    unit = block.unit_name or unit_name_of_title(att)
    return {
        "kind": "scope_only",
        "cert_kind": block.cert_kind or cert_kind_of_title(att),
        "cert_no": None,
        "unit_name": unit,
        "event": None,
        "活动种类": [],
        "fields": {},
        "scope_items": scope,
        "page_start": block.p1,
        "page_end": block.p2,
        "extract_mode": "rule",
    }


def parse_overseas_block(pdf, block, ctx):
    pages = list(block.pages())
    txt = "\n".join(page_text(pdf.pages[p - 1]) for p in pages)
    fields = extract_cert_fields(txt)
    unit = clean_cell(fields.get("单位名称")) or None
    if not unit:
        first = (head_lines(txt, 1) or [""])[0]
        m = re.match(r"^[一二三四五六七八九十]+、\s*([^\s]{2,12})?\s*(.+)$", first)
        unit = (m.group(2) if m else None)
    scope = []
    for p in pages:
        scope += extract_scope_items(pdf.pages[p - 1], p)
    return {
        "kind": "overseas",
        "cert_kind": "境外注册登记",
        "cert_no": parse_cert_no(txt),
        "unit_name": unit,
        "event": "颁发" if "延续" not in txt[:400] else "延续",
        "活动种类": ["设计", "制造"],
        "fields": {
            "法定代表人": norm(fields.get("法定代表人")) or None,
            "单位住所": norm(fields.get("单位住所")) or None,
            "单位所在国": norm(fields.get("单位所在国")) or None,
            "设备类别": split_multi(fields.get("设备类别")),
            "核安全级别": split_multi(fields.get("核安全级别")),
            "设备品种": split_multi(fields.get("设备品种")),
        },
        "scope_items": scope,
        "page_start": block.p1,
        "page_end": block.p2,
        "extract_mode": "rule",
    }


# ================= LLM 兜底 =================
def block_header(ctx, block, k, total):
    return (f"【文档】{ctx.get('doc_title', '')} | doc_id={ctx.get('doc_id', '')} | {ctx.get('doc_url', '')}\n"
            f"【附件】{ctx.get('att_name', '')} | {ctx.get('att_url', '')} | 共{total}块\n"
            f"【块】第{k}/{total}块 | 页 {block.p1}-{block.p2} | 类型={block.kind}\n")


LLM_TRIGGER_BAD_RATIO = 0.3     # 关键字段缺失行占比超过该阈值即触发 LLM 兜底


def needs_llm(rec, block):
    """规则解析失败判定（M3 报告 §6.4 / 补充报告 B4）：
    ① 关键字段缺失（证字号/单位/无范围行）；② 范围行关键列缺失比例过高
    """
    if block.kind == "license" and (not rec["cert_no"] or not rec["unit_name"] or not rec["scope_items"]):
        return True
    if block.kind in ("scope_only", "overseas") and not rec["scope_items"]:
        return True
    rows = rec.get("scope_items") or []
    if rows:
        bad = sum(1 for r in rows if not r.get("设备类别") or not r.get("核安全级别"))
        if bad / len(rows) > LLM_TRIGGER_BAD_RATIO:
            return True
    return False


SCOPE_KEYS = ("设备种类", "设备类别", "设备品种", "民用核设施类别", "活动类别", "核安全级别",
              "能力特征参数", "主要关键工艺", "活动范围及完成形式", "活动场所", "备注", "分包项目")


def normalize_llm_scope_rows(rows, block):
    """规范 LLM 返回的范围行：补齐缺失键、默认源页、按备注补分包项目"""
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        row = {k: r.get(k) for k in SCOPE_KEYS}
        row["能力特征参数"] = row["能力特征参数"] or {}
        for k in ("主要关键工艺", "分包项目"):
            if not isinstance(row[k], list):
                row[k] = split_multi(row[k]) if row[k] else []
        if not row["分包项目"]:
            row["分包项目"] = extract_subcontract(row["备注"])
        row["源页"] = r.get("源页") or r.get("source_page") or block.p1
        if any(row[k] for k in ("设备类别", "设备品种", "民用核设施类别")) or row["能力特征参数"]:
            out.append(row)
    return out


LLM_CHUNK_CHARS = 12000      # 单次 LLM 载荷上限（≈6k tokens），超长块自动分片，避免输出截断/窗口浪费


def _llm_chunks(pdf, pages):
    """把块的页面载荷按字符上限切成若干片（保持页边界）"""
    chunks, cur, cur_len = [], [], 0
    for p in pages:
        payload = page_payload(pdf.pages[p - 1])
        if cur and cur_len + len(payload) > LLM_CHUNK_CHARS:
            chunks.append((cur, cur_len))
            cur, cur_len = [], 0
        cur.append(p)
        cur_len += len(payload)
    if cur:
        chunks.append((cur, cur_len))
    return chunks


def llm_fill(pdf, block, ctx, rec, llm, k, total):
    """LLM 兜底：按片调用（失败隔离，单块异常不影响其他块），合并结果"""
    merged = dict(rec)
    merged.setdefault("fields", {})
    used = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "errors": []}
    rule_rows = rec.get("scope_items") or []
    # 规则行残缺（关键列缺失比例高）→ 以 LLM 行整体替换；否则仅在规则无行时采用
    prefer_llm_rows = bool(rule_rows) and needs_llm(rec, block)
    llm_rows = []
    for ci, (pages, _) in enumerate(_llm_chunks(pdf, list(block.pages())), 1):
        payload = "\n".join(page_payload(pdf.pages[p - 1]) for p in pages)
        header = block_header(ctx, block, k, total) + (f"【分片】{ci} 页 {pages[0]}-{pages[-1]}\n" if ci > 1 else "")
        user = prompts.BLOCK_USER_TPL.format(header=header, payload=payload)
        try:
            data, meta = llm.chat_json(prompts.BLOCK_SYSTEM, user,
                                       tag=f"{ctx.get('doc_id')}#b{k}.{ci}")
        except Exception as e:  # noqa: BLE001
            used["errors"].append(f"片{ci}: {str(e)[:120]}")
            continue
        used["calls"] += 1
        used["prompt_tokens"] += meta.get("prompt_tokens") or 0
        used["completion_tokens"] += meta.get("completion_tokens") or 0
        if not isinstance(data, dict):
            continue
        for key in ("cert_no", "unit_name", "cert_kind"):
            if not merged.get(key) and data.get(key):
                merged[key] = data[key]
        for key, val in (data.get("fields") or {}).items():
            if val and not merged["fields"].get(key):
                merged["fields"][key] = val
        llm_rows += normalize_llm_scope_rows(data.get("scope_items") or [], block)
    if llm_rows and (prefer_llm_rows or not rule_rows):
        merged["scope_items"] = llm_rows
    merged["extract_mode"] = ("rule+llm" if (rule_rows or rec.get("cert_no")) else "llm")
    if prefer_llm_rows and llm_rows:
        merged["extract_mode"] = "llm(补全)"
    merged["llm_usage"] = used
    return merged


# ================= 对外入口 =================
def _parse_block(pdf, block, ctx):
    """按块类型分派到对应解析器"""
    if block.kind == "license":
        return parse_license_block(pdf, block, ctx)
    if block.kind == "scope_only":
        return parse_scope_only_block(pdf, block, ctx)
    return parse_overseas_block(pdf, block, ctx)


def parse_pdf(pdf_path, ctx=None, llm=None, force_llm=False):
    """解析一个附件 PDF → 记录列表（一证一条 / 一页一家一条）"""
    ctx = dict(ctx or {})
    ctx.setdefault("doc_id", os.path.basename(pdf_path).split("_")[0])
    records = []
    with pdfplumber.open(pdf_path) as pdf:
        blocks = split_blocks(pdf)
        total = len(blocks)
        for k, b in enumerate(blocks, 1):
            if b.kind not in ("license", "scope_only", "overseas"):
                continue
            try:
                rec = _parse_block(pdf, b, ctx)
            except Exception as e:  # noqa: BLE001 单块失败不影响整份附件
                records.append({"kind": "block_error", "block_index": k, "page_start": b.p1,
                                "page_end": b.p2, "error": f"{type(e).__name__}: {str(e)[:200]}",
                                "doc_id": ctx.get("doc_id"), "att_url": ctx.get("att_url"),
                                "doc_url": ctx.get("doc_url"), "unit_name": None, "scope_items": [],
                                "cert_kind": b.cert_kind, "att_name": ctx.get("att_name")})
                continue
            rec["block_index"] = k
            rec["block_total"] = total
            rec.update({key: ctx.get(key) for key in ("doc_id", "doc_title", "doc_url", "doc_date", "att_name", "att_url")})
            if llm is not None and (force_llm or needs_llm(rec, b)):
                rec = llm_fill(pdf, b, ctx, rec, llm, k, total)
            # 纯名单/说明类附件（无单位、无范围行）不产出记录
            if not rec.get("unit_name") and not rec.get("scope_items"):
                continue
            records.append(rec)
    return records


def main():
    ap = argparse.ArgumentParser(description="L3 单文件 PDF 解析（调试用）")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--llm", action="store_true", help="启用 LLM 兜底")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    client = None
    if args.llm:
        from haf.llm import LLMClient
        client = LLMClient(verbose=True)
    recs = parse_pdf(args.pdf, ctx={"att_name": os.path.basename(args.pdf)}, llm=client)
    for r in recs:
        print(f"\n—— {r['kind']} | {r['cert_kind']} | {r['cert_no']} | {r['unit_name']} "
              f"| 页{r['page_start']}-{r['page_end']} | 模式={r['extract_mode']} | 范围行 {len(r['scope_items'])}")
        if args.verbose:
            print(json.dumps({k: v for k, v in r.items() if k != "scope_items"},
                             ensure_ascii=False, indent=1))
            for s in r["scope_items"][:3]:
                print("   ", json.dumps(s, ensure_ascii=False))
    if client:
        print("\n" + client.summary())


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    main()
