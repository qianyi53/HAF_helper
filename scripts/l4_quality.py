# -*- coding: utf-8 -*-
"""L4 数据质量速查（开发/测试用）"""
import json
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
con = sqlite3.connect("data/db/haf.db")
con.row_factory = sqlite3.Row
q = lambda sql: con.execute(sql).fetchall()
one = lambda sql: con.execute(sql).fetchone()[0]

print("状态分布:", [dict(r) for r in q("SELECT 状态, COUNT(*) n FROM licenses GROUP BY 状态 ORDER BY n DESC")])
print("\n非有效许可证样例:")
for r in q("SELECT 单位名称, 证字号, 状态, 状态依据 FROM licenses WHERE 状态<>'有效' LIMIT 8"):
    print(f"  - {r['单位名称']} {r['证字号']} [{r['状态']}] {r['状态依据']}")

print("\n范围行质量:")
print("  总行数            ", one("SELECT COUNT(*) FROM scope_items"))
print("  缺设备类别        ", one("SELECT COUNT(*) FROM scope_items WHERE 设备类别 IS NULL OR 设备类别=''"))
print("  缺设备品种        ", one("SELECT COUNT(*) FROM scope_items WHERE 设备品种 IS NULL OR 设备品种=''"))
print("  缺核安全级别      ", one("SELECT COUNT(*) FROM scope_items WHERE 核安全级别 IS NULL OR 核安全级别=''"))
print("  带能力特征参数    ", one("SELECT COUNT(*) FROM scope_items WHERE 能力特征参数 NOT IN ('{}','')"))
print("  带活动范围与场所  ", one("SELECT COUNT(*) FROM scope_items WHERE 活动范围及完成形式 IS NOT NULL"))
print("  缺源页码          ", one("SELECT COUNT(*) FROM scope_items WHERE 源页码 IS NULL"))

print("\n变更:", one("SELECT COUNT(*) FROM changes"), "条 |",
      "含新增的", one("SELECT COUNT(*) FROM changes WHERE 变更明细 LIKE '%\"新增\": [{%'"), "|",
      "含修改的", one("SELECT COUNT(*) FROM changes WHERE 变更明细 LIKE '%\"修改\": [{%'"), "|",
      "纯形式变更", one("SELECT COUNT(*) FROM changes WHERE 变更明细 LIKE '%\"新增\": [],%\"删除\": [],%\"修改\": []%'"))

print("\n解析元信息:")
meta = json.load(open("data/parsed_records.json", encoding="utf-8"))["meta"]
print("  附件 ok:", meta["attachments_ok"], "| 记录:", meta["records"],
      "| LLM 调用:", (meta["llm"] or {}).get("calls"), "次 | 耗时:", meta["elapsed_seconds"], "s")
print("  解析错误:", len(meta["errors"]))
for k, v in list(meta["errors"].items())[:3]:
    print(f"    {k[:80]} -> {v[:90]}")

print("\n许可证可读性抽样（view_license_current）:")
for r in q("SELECT 单位名称, 证字号, 证件类型, 状态, 设备类别汇总, 有效期止, 变更次数 FROM view_license_current LIMIT 3"):
    print("  ", " | ".join(str(r[k]) for k in r.keys()))
