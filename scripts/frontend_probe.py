# -*- coding: utf-8 -*-
"""前端查询页面需求分析用探测：盘点现有交付库对两类查询的支撑度（分析用，不属落地代码）

两类查询：
  ① 按设备类别/品种 → 最新相关许可证 + 详细参数
  ② 按单位 → 最新许可证情况 + 设备详细参数
"""
import json
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DB = "data/db/haf.db"
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
q = lambda sql, args=(): con.execute(sql, args).fetchall()
one = lambda sql, args=(): con.execute(sql, args).fetchone()[0]


def title(t):
    print("\n" + "=" * 96)
    print(f"### {t}")


title("0. 交付库规模")
for t in ("licenses", "license_versions", "scope_items", "changes", "registry_scopes",
          "view_license_current", "view_device_index"):
    print(f"  {t:22s} {one(f'SELECT COUNT(*) FROM {t}')}")

title("1. 查询流①：设备维度可查性（当前版本口径）")
print("  设备类别数:", one("SELECT COUNT(DISTINCT 设备类别) FROM view_device_index"))
print("  设备品种数:", one("SELECT COUNT(DISTINCT 设备品种) FROM view_device_index WHERE 设备品种 IS NOT NULL"))
print("  范围行（当前版本）缺失情况:",
      "类别", one("SELECT COUNT(*) FROM view_device_index WHERE 设备类别 IS NULL"),
      "| 品种", one("SELECT COUNT(*) FROM view_device_index WHERE 设备品种 IS NULL"),
      "| 级别", one("SELECT COUNT(*) FROM view_device_index WHERE 核安全级别 IS NULL"))
print("\n  设备类别 Top10（类别 → 品种数 / 许可证明细行 / 单位数）:")
for r in q("""SELECT 设备类别, COUNT(DISTINCT 设备品种) AS 品种数, COUNT(*) AS 明细行,
                     COUNT(DISTINCT 持证单位) AS 单位数
              FROM view_device_index GROUP BY 设备类别 ORDER BY 明细行 DESC LIMIT 10"""):
    print(f"    {str(r['设备类别'])[:24]:26s} 品种 {r['品种数']:3d} | 行 {r['明细行']:4d} | 单位 {r['单位数']:3d}")

print("\n  示例：设备类别=阀门 的品种分布（Top8）:")
for r in q("""SELECT 设备品种, COUNT(*) AS 行数, COUNT(DISTINCT 持证单位) AS 单位数
              FROM view_device_index WHERE 设备类别='阀门'
              GROUP BY 设备品种 ORDER BY 行数 DESC LIMIT 8"""):
    print(f"    {str(r['设备品种'])[:26]:28s} 行 {r['行数']:4d} | 单位 {r['单位数']:3d}")

title("2. 查询流①的“最新”排序（生效日期）")
print("  当前版本按生效年份分布:")
for r in q("""SELECT substr(v.生效日期,1,4) AS 年, COUNT(*) AS n FROM licenses l
              JOIN license_versions v ON v.version_id = l.当前版本id
              WHERE v.生效日期 IS NOT NULL GROUP BY 年 ORDER BY 年 DESC"""):
    print(f"    {r['年']}  {r['n']}")

print("\n  排序采样（隔离阀，按生效日期倒序 Top5）:")
for r in q("""SELECT 持证单位, 证字号, 核安全级别, 能力特征参数, 数据来源事件, 数据生效日期
              FROM view_device_index WHERE 设备类别='阀门' AND 设备品种='隔离阀'
              ORDER BY 数据生效日期 DESC LIMIT 5"""):
    print(f"    {r['数据生效日期']} [{r['数据来源事件']}] {str(r['持证单位'])[:18]} {r['证字号']} "
          f"{str(r['核安全级别'])[:8]} {str(r['能力特征参数'])[:34]}")

title("3. 查询流②：单位维度可查性")
print("  持证单位数:", one("SELECT COUNT(DISTINCT 单位名称) FROM licenses"))
print("  单位持有多个许可证的分布:")
for r in q("""SELECT 证数, COUNT(*) AS 单位数 FROM (
                  SELECT 单位名称, COUNT(*) AS 证数 FROM licenses GROUP BY 单位名称)
              GROUP BY 证数 ORDER BY 证数"""):
    print(f"    {r['证数']} 张证的单位: {r['单位数']} 家")
print("\n  证数最多的单位 Top5:")
for r in q("""SELECT 单位名称, COUNT(*) AS 证数, COUNT(DISTINCT 证件类型) AS 证件类型数
              FROM licenses GROUP BY 单位名称 ORDER BY 证数 DESC LIMIT 5"""):
    print(f"    {str(r['单位名称'])[:40]:42s} {r['证数']} 证 / {r['证件类型数']} 类型")

print("\n  示例：'中核苏阀' 单位卡片所需字段:")
for r in q("""SELECT 证字号, 证件类型, 状态, 有效期止, 版本数, 变更次数, 最近变更日期, 最近变更摘要
              FROM view_license_current WHERE 单位名称 LIKE '%中核苏阀%'"""):
    print(f"    {r['证字号']} | {r['证件类型']} | {r['状态']} | 至 {r['有效期止']} | "
          f"版本 {r['版本数']} 变更 {r['变更次数']} | {str(r['最近变更摘要'])[:40]}")

print("\n  该单位的设备参数明细（行数按类别）:")
for r in q("""SELECT s.设备类别, COUNT(*) AS 行数 FROM scope_items s
              JOIN licenses l ON l.license_id = s.license_id
              WHERE l.单位名称 LIKE '%中核苏阀%' AND s.version_id = l.当前版本id
              GROUP BY s.设备类别"""):
    print(f"    {r['设备类别']}  {r['行数']}")

title("4. 状态 / 撤稿 / 追溯字段完备性")
for r in q("SELECT 状态, COUNT(*) n FROM licenses GROUP BY 状态"):
    print(f"  状态 {r['状态']}: {r['n']}")
print("  有数据标志:", one("SELECT COUNT(*) FROM licenses WHERE 数据标志 IS NOT NULL"))
print("  缺官网链接:", one("SELECT COUNT(*) FROM licenses WHERE 官网链接 IS NULL"),
      "| 缺附件页码:", one("SELECT COUNT(*) FROM licenses WHERE 附件页码 IS NULL"))
print("  明细缺源页码:", one("SELECT COUNT(*) FROM scope_items WHERE 源页码 IS NULL"))

title("5. 变更信息（供“相关变更”展示）")
print("  变更总数:", one("SELECT COUNT(*) FROM changes"),
      "| 有摘要:", one("SELECT COUNT(*) FROM changes WHERE 变更摘要 IS NOT NULL"),
      "| 有明细:", one("SELECT COUNT(*) FROM changes WHERE 变更明细 IS NOT NULL"))
print("  变更按年份分布:")
for r in q("SELECT substr(变更日期,1,4) AS 年, COUNT(*) n FROM changes GROUP BY 年 ORDER BY 年 DESC"):
    print(f"    {r['年']}  {r['n']}")

title("6. 性能与索引（前端响应预期的依据）")
for t in ("licenses", "license_versions", "scope_items", "changes"):
    idx = [r["name"] for r in q(f"PRAGMA index_list('{t}')")]
    print(f"  {t:18s} 索引: {idx}")
plan = q("EXPLAIN QUERY PLAN SELECT * FROM scope_items WHERE 设备类别='阀门' AND 设备品种='隔离阀'")
print("  设备条件查询计划:", [r[-1] for r in plan])
plan = q("EXPLAIN QUERY PLAN SELECT * FROM licenses WHERE 单位名称 LIKE '%中核苏阀%'")
print("  单位模糊查询计划:", [r[-1] for r in plan])

title("7. 静态化体积估算（若前端走静态 JSON）")
view_rows = {name: [dict(r) for r in q(f"SELECT * FROM {name}")]
             for name in ("view_license_current", "view_device_index")}
for name, rows in view_rows.items():
    size = len(json.dumps(rows, ensure_ascii=False).encode("utf-8"))
    print(f"  {name}: {len(rows)} 行, JSON {size/1024:.0f} KB")
print("  库文件:", f"{os.path.getsize(DB)/1024/1024:.1f} MB")
