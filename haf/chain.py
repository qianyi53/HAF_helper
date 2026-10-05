# -*- coding: utf-8 -*-
"""haf/chain.py — L3 版本链 / 变更 diff / 许可证状态判定

依据 M3 报告 §6.5：
  许可证身份 = (规范化单位名, 证件类型)；证字号是「版本属性」（延续换证会变号）
  版本来源事件 ∈ {颁发, 延续, 不予延续, 变更, 注销}
  变更内容 = 相邻版本 scope_items 行级 diff（附件是"变更后全量表"，无差异标注）
  状态     = 有效 / 无效(已到期) / 无效(不予延续) / 已注销 / 待核实
"""
import datetime as dt
import hashlib
import json
import re

CERT_TYPE_BY_KIND = {
    "设计": "民用核安全设备设计许可证",
    "制造": "民用核安全设备制造许可证",
    "安装": "民用核安全设备安装许可证",
    "境外注册登记": "民用核安全设备活动境外单位注册登记确认书",
}
SCOPE_ROW_KEY = ("设备类别", "设备品种", "核安全级别", "活动场所")
DIFF_FIELDS = ("核安全级别", "能力特征参数", "主要关键工艺", "活动范围及完成形式", "活动场所", "备注")


def norm_unit(name):
    """单位名规范化：去空白/全角空格/常见标点差异，用于版本链归组（保留原始名另存）"""
    if not name:
        return ""
    s = re.sub(r"[\s\u3000]+", "", name)
    s = s.replace("（", "(").replace("）", ")").replace("，", ",").replace("“", '"').replace("”", '"')
    return s


def license_id_of(unit, cert_type):
    """稳定许可证 ID：规范化单位名 + 证件类型（证字号变化不影响）"""
    h = hashlib.md5(f"{norm_unit(unit)}|{cert_type}".encode()).hexdigest()[:10]
    return f"L{h}"


def version_id_of(license_id, seq):
    return f"{license_id}V{seq:02d}"


def cert_type_of(rec):
    kind = rec.get("cert_kind") or ""
    return CERT_TYPE_BY_KIND.get(kind, f"其他:{kind}")


def version_date(rec):
    """版本生效日期：成文日期 > 生成日期 > 空"""
    for key in ("成文日期", "生成日期", "doc_date"):
        v = rec.get(key)
        if v:
            return str(v)[:10]
    return ""


def scope_row_key(row):
    return tuple((row.get(k) or "") for k in SCOPE_ROW_KEY)


def params_readable(params, activity=None):
    """能力特征参数 → 可读串（供视图人读）"""
    if not params:
        return ""
    label = {"公称通径": "DN", "设计压力": "P", "设计温度": "T", "载荷等级": "载荷",
             "结构形式": "结构形式", "典型设备名称": "典型设备", "材料类别": "材料",
             "成品重量": "重量", "壁厚": "壁厚", "管板厚度": "管板厚度", "堆型功率": "堆型功率"}
    parts = []
    for k, v in params.items():
        name = label.get(k, k)
        if isinstance(v, dict) and "值" in v:
            unit = v.get("单位", "")
            s = f"{name}{'≤' if v.get('上限') else '='}{v['值']}"
            if unit and unit != name:          # 避免 "DN≤1050DN" 这类重复单位
                s += unit
            add = v.get("附加") or {}
            for ak, av in add.items():
                s += f"（{ak}≤{av.get('值')}）"
            parts.append(s)
        elif isinstance(v, list):
            parts.append(f"{name}:{'、'.join(str(x) for x in v)}")
        elif isinstance(v, dict):
            parts.append(f"{name}:{v.get('原文', '')}")
        else:
            parts.append(f"{name}:{v}")
    return " / ".join(parts)


def diff_versions(prev_rows, curr_rows):
    """相邻版本行级 diff：新增/删除/修改（行键 = 设备类别+品种+级别+场所）"""
    prev_map = {scope_row_key(r): r for r in prev_rows}
    curr_map = {scope_row_key(r): r for r in curr_rows}
    added = [curr_map[k] for k in curr_map if k not in prev_map]
    removed = [prev_map[k] for k in prev_map if k not in curr_map]
    modified = []
    for k in curr_map.keys() & prev_map.keys():
        diffs = []
        for f in DIFF_FIELDS:
            a, b = prev_map[k].get(f), curr_map[k].get(f)
            if json.dumps(a, ensure_ascii=False, sort_keys=True) != json.dumps(b, ensure_ascii=False, sort_keys=True):
                diffs.append({"字段": f, "变更前": a, "变更后": b})
        if diffs:
            modified.append({"行键": list(k), "字段变更": diffs})
    return {"新增": added, "删除": removed, "修改": modified}


def summarize_diff(diff, cert_type=""):
    """规则式变更摘要（LLM P1 可选精细化，见 prompts.CHANGE_*）"""
    n_add, n_del, n_mod = len(diff["新增"]), len(diff["删除"]), len(diff["修改"])
    if not (n_add or n_del or n_mod):
        return "范围表内容与上一版本一致（形式变更）"
    bits = []
    if n_add:
        bits.append(f"新增 {n_add} 项设备范围")
    if n_del:
        bits.append(f"删除 {n_del} 项设备范围")
    if n_mod:
        fields = {d["字段"] for m in diff["修改"] for d in m["字段变更"]}
        bits.append(f"修改 {n_mod} 项（涉及 {'、'.join(sorted(fields))}）")
    return f"{cert_type}：" + "；".join(bits)


CANCEL_REF_RE = re.compile(r"同时注销(.{4,200}?)(?:许可证|确认书)")
COUNTRY_PREFIX_RE = re.compile(r"^(俄罗斯|美国|法国|德国|英国|日本|韩国|加拿大|意大利|捷克|瑞士|瑞典|西班牙)")


def parse_cancel_span(span):
    """从"<单位名>[持有的]民用核安全设备…[证照名]"中切出单位名"""
    unit = re.split(r"持有的|所持有的|持有的原|的民用核安全设备|《中华人民共和国民用核安全设备|民用核安全设备", span)[0]
    unit = re.sub(r"[《》〈〉〔〕\[\]（）()\s]+", "", unit).strip("，。、；:：")
    return COUNTRY_PREFIX_RE.sub("", unit)


def extract_cancel_refs(labeled_docs):
    """从 L2 标注为"注销"的文档正文里抽取"同时注销××原许可证"引用

    返回 [{被注销单位, 证件关键词, 来源doc_id, 日期, 官网链接}]
    （M3 报告：2021 年以来无独立注销通知，注销信息均内嵌在颁发/延续正文中）
    """
    refs = []
    for d in labeled_docs:
        labels = d.get("labels") or []
        if "事件:注销" not in labels:
            continue
        for m in CANCEL_REF_RE.finditer(d.get("正文") or ""):
            unit = parse_cancel_span(m.group(1))
            if len(norm_unit(unit)) >= 4:
                refs.append({"被注销单位": unit,
                             "来源doc_id": d.get("doc_id"),
                             "日期": (d.get("成文日期") or d.get("生成日期") or "")[:10],
                             "官网链接": d.get("链接")})
    return refs


def determine_status(license_rec, versions, today=None, has_cancel_event=False,
                     cancel_ref=None):
    """状态判定（M3 报告 §6.5）"""
    today = today or dt.date.today().isoformat()
    if not versions:
        return "待核实", ""
    last = versions[-1]
    event = last.get("来源事件") or ""
    if event == "不予延续":
        return "无效", f"不予延续（{last.get('来源doc_id')}）"
    if cancel_ref and (cancel_ref.get("日期") or "") > (last.get("生效日期") or ""):
        return "已注销", (f"换证/收回时注销（{cancel_ref.get('来源doc_id')}，"
                        f"{cancel_ref.get('日期')}；原证自 {last.get('生效日期')} 起）")
    if has_cancel_event and event in ("颁发", "延续", "变更"):
        return "已注销", f"换证/收回时注销（{last.get('来源doc_id')}）"
    end = license_rec.get("有效期止")
    if end and end < today and event != "变更":
        return "无效", f"有效期已过（{end}）"
    if not end and event == "变更":
        return "有效", "变更不改变有效性（有效期未知，沿用上一版本）"
    return "有效", f"最近事件：{event}（{last.get('生效日期')}）"


def build_model(parsed_records, docs_index=None, today=None, cancel_refs=None,
                missing_doc_ids=None):
    """把解析记录组装成 license/version/scope/change 模型

    parsed_records: 每个元素为 haf.pdf_parser.parse_pdf 返回的单条记录，已带 doc_id/doc_date/doc_url/att_* 上下文
    docs_index:     {doc_id: {官网链接, 事件标签, 标题, 文号, 生成日期, 成文日期}}
    """
    docs_index = docs_index or {}
    groups = {}     # (unit_key, cert_type) -> [版本草稿]
    for rec in parsed_records:
        if rec.get("kind") not in ("license", "scope_only", "overseas"):
            continue
        unit = rec.get("unit_name")
        if not unit:
            continue
        cert_type = cert_type_of(rec)
        key = (norm_unit(unit), cert_type)
        event = rec.get("event") or ("变更" if rec.get("kind") == "scope_only" else "颁发")
        groups.setdefault(key, []).append({
            "单位名称": unit,
            "证件类型": cert_type,
            "来源事件": event,
            "来源doc_id": rec.get("doc_id"),
            "证字号": rec.get("cert_no"),
            "生效日期": version_date(rec),
            "有效期原文": (rec.get("fields") or {}).get("有效期原文"),
            "有效期止": (rec.get("fields") or {}).get("有效期止"),
            "设备类别汇总": (rec.get("fields") or {}).get("设备类别") or [],
            "核安全级别汇总": (rec.get("fields") or {}).get("核安全级别") or [],
            "活动种类": rec.get("活动种类") or [],
            "单位所在国": (rec.get("fields") or {}).get("单位所在国"),
            "活动类别": (rec.get("fields") or {}).get("活动类别"),
            "scope_items": rec.get("scope_items") or [],
            "页码起": rec.get("page_start"),
            "页码止": rec.get("page_end"),
            "附件链接": rec.get("att_url"),
            "抽取模式": rec.get("extract_mode"),
        })

    licenses, versions, scope_items, changes = [], [], [], []
    for (unit_key, cert_type), drafts in groups.items():
        drafts.sort(key=lambda d: (d["生效日期"] or "0000-00-00", str(d.get("来源doc_id") or "")))
        license_id = license_id_of(unit_key, cert_type)
        seq = 0
        prev_rows = None
        for d in drafts:
            seq += 1
            vid = version_id_of(license_id, seq)
            # 证字号继承：变更范围表不带证字号，沿用上一版本
            if not d["证字号"] and versions:
                same = [v for v in versions if v["license_id"] == license_id and v["证字号"]]
                if same:
                    d["证字号"] = same[-1]["证字号"]
            vrow = {
                "version_id": vid,
                "license_id": license_id,
                "单位名称": d["单位名称"],
                "证件类型": cert_type,
                "来源事件": d["来源事件"],
                "来源doc_id": d["来源doc_id"],
                "生效日期": d["生效日期"],
                "证字号": d["证字号"],
                "附件链接": d["附件链接"],
                "页码起": d["页码起"],
                "页码止": d["页码止"],
                "抽取模式": d["抽取模式"],
                "文档状态": "官网未见" if d["来源doc_id"] in (missing_doc_ids or set()) else None,
                "范围行数": len(d["scope_items"]),
            }
            # 变更 diff
            if d["来源事件"] == "变更" and prev_rows is not None:
                diff = diff_versions(prev_rows, d["scope_items"])
                # 一份变更通知可能同时变更同一单位的多个许可证（如设计+制造），故 change_id 需带许可证
                cid = f"C{d['来源doc_id']}-{license_id}"
                vrow["change_id"] = cid
                changes.append({
                    "change_id": cid,
                    "license_id": license_id,
                    "单位名称": d["单位名称"],
                    "证件类型": cert_type,
                    "证字号": d["证字号"],
                    "变更日期": d["生效日期"],
                    "变更类型": "许可活动范围",
                    "变更摘要": summarize_diff(diff, cert_type),
                    "变更明细": diff,
                    "来源doc_id": d["来源doc_id"],
                    "官网链接": (docs_index.get(d["来源doc_id"]) or {}).get("官网链接"),
                    "附件页码": f"{d['页码起']}-{d['页码止']}",
                    "抽取模式": d["抽取模式"],
                    "diff基础版本": versions[-1]["version_id"] if versions else None,
                })
            versions.append(vrow)
            for i, srow in enumerate(d["scope_items"], 1):
                scope_items.append({
                    "scope_item_id": f"{vid}-S{i:03d}",
                    "version_id": vid,
                    "license_id": license_id,
                    "设备种类": srow.get("设备种类"),
                    "设备类别": srow.get("设备类别"),
                    "设备品种": srow.get("设备品种"),
                    "民用核设施类别": srow.get("民用核设施类别"),
                    "活动类别": srow.get("活动类别"),
                    "核安全级别": srow.get("核安全级别"),
                    "能力特征参数": srow.get("能力特征参数") or {},
                    "能力特征参数可读": params_readable(srow.get("能力特征参数") or {}),
                    "主要关键工艺": srow.get("主要关键工艺") or [],
                    "活动范围及完成形式": srow.get("活动范围及完成形式"),
                    "活动场所": srow.get("活动场所"),
                    "备注": srow.get("备注"),
                    "分包项目": srow.get("分包项目") or [],
                    "源页码": srow.get("源页"),
                })
            prev_rows = d["scope_items"]

        my_versions = [v for v in versions if v["license_id"] == license_id]
        my_versions.sort(key=lambda v: (v["生效日期"] or "0000-00-00", v["version_id"]))
        last = my_versions[-1]
        cancel_events = [c for c in changes if c["license_id"] == license_id and "注销" in (c.get("变更类型") or "")]
        info = docs_index.get(last["来源doc_id"]) or {}
        lrow = {
            "license_id": license_id,
            "单位名称": last["单位名称"],
            "单位所在国": next((d["单位所在国"] for d in reversed(drafts) if d["单位所在国"]), None),
            "证字号": last["证字号"],
            "证件类型": cert_type,
            "活动种类": json.dumps(sorted({a for d in drafts for a in d["活动种类"]}), ensure_ascii=False),
            "发证日期": my_versions[0]["生效日期"],
            "有效期起": my_versions[0]["生效日期"],
            "有效期止": next((d["有效期止"] for d in reversed(drafts) if d["有效期止"]), None),
            "有效期原文": next((d["有效期原文"] for d in reversed(drafts) if d["有效期原文"]), None),
            "设备类别汇总": json.dumps(sorted({x for d in drafts for x in d["设备类别汇总"]}), ensure_ascii=False),
            "核安全级别汇总": json.dumps(sorted({x for d in drafts for x in d["核安全级别汇总"]}), ensure_ascii=False),
            "当前版本id": last["version_id"],
            "版本数": len(my_versions),
            "来源doc_id": last["来源doc_id"],
            "官网链接": info.get("官网链接"),
            "附件链接": last["附件链接"],
            "附件页码": f"{last['页码起']}-{last['页码止']}",
            "更新日期": last["生效日期"],
        }
        # 注销引用匹配：以"被注销单位"规范化名比对（同单位可能有设计+制造多证）
        ref = None
        for cr in (cancel_refs or []):
            if norm_unit(cr["被注销单位"]) and norm_unit(cr["被注销单位"]) == unit_key:
                if not ref or (cr.get("日期") or "") > (ref.get("日期") or ""):
                    ref = cr
        st, reason = determine_status(lrow, my_versions, today=today,
                                      has_cancel_event=bool(cancel_events), cancel_ref=ref)
        lrow["状态"], lrow["状态依据"] = st, reason
        # 撤稿同步（M6）：来源文函在官网列表中消失时，保留解析结果但打标志并降级为"待核实"
        missing_doc_ids = missing_doc_ids or set()
        lrow["数据标志"] = None
        cur_doc = lrow.get("来源doc_id") or lrow.get("当前版本doc_id")
        if cur_doc and cur_doc in missing_doc_ids:
            lrow["数据标志"] = "来源文函官网已撤下"
            if st == "有效":
                lrow["状态"] = "待核实"
                lrow["状态依据"] = f"来源文函在官网列表已不见（{cur_doc}），原依据：{reason}"
        licenses.append(lrow)

    # 境外注册登记范围（单独建表，字段名按确认书口径）
    registry, lic_by_id = [], {l["license_id"]: l for l in licenses}
    for s in scope_items:
        lic = lic_by_id.get(s["license_id"])
        if not lic or "境外单位注册登记" not in (lic["证件类型"] or ""):
            continue
        registry.append({
            "registry_scope_id": f"R{s['scope_item_id']}",
            "version_id": s["version_id"],
            "license_id": s["license_id"],
            "单位名称": lic["单位名称"],
            "单位所在国": lic.get("单位所在国"),
            "活动类别": s.get("活动类别") or "设计、制造",
            "设备类别": s["设备类别"],
            "设备品种": s["设备品种"],
            "核安全级别": s["核安全级别"],
            "备注": s["备注"],
            "源页码": s["源页码"],
        })
    return {"licenses": licenses, "license_versions": versions,
            "scope_items": scope_items, "changes": changes, "registry_scopes": registry}
