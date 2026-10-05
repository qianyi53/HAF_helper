# -*- coding: utf-8 -*-
"""数据字典探测（合并版）：导出 haf.db 的真实结构、枚举分布、JSON 字段盘点与异常值

用途：编写/复核 docs/06-数据字典.md 时一键复跑，确保文档与实际交付库一致。
用法: python -X utf8 scripts/db_dict_probe.py
"""
import json
import sqlite3
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DB = "data/db/haf.db"
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
q = lambda sql: con.execute(sql).fetchall()
one = lambda sql: con.execute(sql).fetchone()[0]


def head(t):
    print("\n" + "=" * 96)
    print(f"### {t}")


head("1. 对象清单与行数")
for r in q("SELECT type, name FROM sqlite_master WHERE type IN ('table','view') ORDER BY type, name"):
    n = one("SELECT COUNT(*) FROM " + r["name"])
    print(f"  {r['type']:6s} {r['name']:22s} {n} 行")

head("2. 列结构 / 外键 / 索引")
for t in ("documents", "licenses", "license_versions", "scope_items", "changes", "registry_scopes"):
    cols = q(f"PRAGMA table_info('{t}')")
    print(f"\n  [{t}] {len(cols)} 列: " + ", ".join(f"{c['name']}({c['type']})" for c in cols))
    fks = q(f"PRAGMA foreign_key_list('{t}')")
    if fks:
        print("    外键: " + "; ".join(f"{fk['from']}→{fk['table']}.{fk['to']}" for fk in fks))
    idx = [r["name"] for r in q(f"PRAGMA index_list('{t}')") if not r["name"].startswith("sqlite_")]
    if idx:
        print("    索引: " + ", ".join(idx))
for v in ("view_license_current", "view_device_index"):
    print(f"\n  [{v}] 列: " + " | ".join(c["name"] for c in q(f"PRAGMA table_info('{v}')")))

head("3. 枚举（标签型）字段取值分布")
for t, c in [("licenses", "状态"), ("licenses", "证件类型"), ("licenses", "数据标志"),
             ("license_versions", "来源事件"), ("license_versions", "抽取模式"), ("license_versions", "文档状态"),
             ("scope_items", "活动类别"), ("registry_scopes", "活动类别"),
             ("changes", "变更类型"), ("documents", "官网状态")]:
    print(f"  {t}.{c}: " + " | ".join(f"{r['v']!r}×{r['n']}"
                                      for r in q(f"SELECT {c} AS v, COUNT(*) n FROM {t} GROUP BY v ORDER BY n DESC")))

head("4. JSON 字段盘点（标签型 vs 数据型）")


def array_values(table, col):
    c = Counter()
    for r in q(f"SELECT {col} FROM {table}"):
        try:
            for v in json.loads(r[0] or "[]"):
                c[v] += 1
        except Exception:  # noqa: BLE001
            pass
    return c


for table, col in [("licenses", "活动种类"), ("licenses", "设备类别汇总"), ("licenses", "核安全级别汇总"),
                   ("scope_items", "主要关键工艺")]:
    c = array_values(table, col)
    print(f"\n  [{table}.{col}] 标签型 JSON 数组 | 去重 {len(c)}")
    print("    Top15:", dict(c.most_common(15)) if col != "活动种类" else dict(c))

keys, shapes = Counter(), Counter()
for r in q("SELECT 能力特征参数 FROM scope_items"):
    try:
        d = json.loads(r[0] or "{}")
    except Exception:  # noqa: BLE001
        continue
    for k, v in d.items():
        keys[k] += 1
        if isinstance(v, list):
            shapes[f"{k}:list"] += 1
        elif isinstance(v, dict) and "值" in v:
            shapes[f"{k}:数值" + ("+附加" if "附加" in v else "")] += 1
        elif isinstance(v, dict) and "原文" in v:
            shapes[f"{k}:文本"] += 1
print(f"\n  [scope_items.能力特征参数] 数据型 JSON 对象 | 参数名 {len(keys)} 种")
print("    Top20:", dict(keys.most_common(20)))
print("    值形态 Top10:", dict(shapes.most_common(10)))

print("\n  [scope_items.分包项目] 数据型（自由文本列表）样本:")
for r in q("SELECT 分包项目 FROM scope_items WHERE 分包项目 NOT IN ('[]','') LIMIT 3"):
    print("   ", str(r[0])[:130])

head("4b. 变更明细构成（按 JSON 解析，非文本匹配）")
stat = {"新增": 0, "删除": 0, "修改": 0}
for r in q("SELECT 变更明细 FROM changes"):
    try:
        d = json.loads(r[0] or "{}")
    except Exception:  # noqa: BLE001
        continue
    for k in stat:
        if d.get(k):
            stat[k] += 1
print("  含非空 新增/删除/修改 的变更条数:", stat, "| 总变更:", one("SELECT COUNT(*) FROM changes"))

head("5. 变更明细结构（数据型）")
d = json.loads(q("SELECT 变更明细 FROM changes LIMIT 1")[0][0])
print("  顶层键:", list(d.keys()))
for k in ("新增", "删除", "修改"):
    v = d.get(k) or []
    print(f"  {k}: 该类元素键 = {list(v[0].keys()) if v else '—'}")
mod = next((m for m in (d.get("修改") or [])), None)
if mod:
    print("  修改[0] 样例:", json.dumps(mod, ensure_ascii=False)[:260])
print("  含'修改'的变更条数:", one("SELECT COUNT(*) FROM changes WHERE 变更明细 LIKE '%修改%'"))
print("  diff基础版本为空:", one("SELECT COUNT(*) FROM changes WHERE diff基础版本 IS NULL"))

head("6. SQLite JSON1 能力验证（供文档示例）")
print("  sqlite 版本:", sqlite3.sqlite_version)
print("  json_extract:", con.execute("SELECT json_extract('{\"a\":1}', '$.a')").fetchone()[0])
print("  标签筛选（活动种类含'设计'的许可证数）:",
      one("SELECT COUNT(DISTINCT license_id) FROM licenses l, json_each(l.活动种类) WHERE json_each.value='设计'"))
print("  参数展开示例:", [dict(r) for r in con.execute(
    "SELECT json_each.key AS 参数名, json_extract(json_each.value,'$.值') AS 数值, "
    "json_extract(json_each.value,'$.单位') AS 单位 FROM scope_items s, json_each(s.能力特征参数) "
    "WHERE s.设备类别='阀门' LIMIT 2")])

head("7. 异常值核查（前端容错清单依据）")
for r in q("SELECT license_id, 单位名称, 证字号, 证件类型, 状态 FROM licenses WHERE 证件类型 LIKE '其他%'"):
    print("  证件类型异常:", dict(r))
fallback = variant = 0
for r in q("SELECT 能力特征参数 FROM scope_items"):
    try:
        d = json.loads(r[0] or "{}")
    except Exception:  # noqa: BLE001
        continue
    if "值" in d:
        fallback += 1
    if any(("（" in k or "(" in k) for k in d):
        variant += 1
print("  能力参数顶层兜底键'值'条数:", fallback, "| 含括号单位变体键条数:", variant)
print("  当前版本口径缺失:",
      "类别", one("SELECT COUNT(*) FROM view_device_index WHERE 设备类别 IS NULL"),
      "| 品种", one("SELECT COUNT(*) FROM view_device_index WHERE 设备品种 IS NULL"),
      "| 级别", one("SELECT COUNT(*) FROM view_device_index WHERE 核安全级别 IS NULL"))
print("  链接/页码完备性: 缺链接", one("SELECT COUNT(*) FROM licenses WHERE 官网链接 IS NULL"),
      "| 缺附件页码", one("SELECT COUNT(*) FROM licenses WHERE 附件页码 IS NULL"),
      "| 明细缺源页码", one("SELECT COUNT(*) FROM scope_items WHERE 源页码 IS NULL"))
