# -*- coding: utf-8 -*-
"""L4 验收：按用户三个查询诉求在 SQLite 交付库上做实际查询（开发/测试用）"""
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DB = "data/db/haf.db"


def show(title, sql, args=(), limit=8):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(sql, args).fetchall()
    print("\n" + "=" * 100)
    print(f"### {title}  （{len(rows)} 行，显示前 {min(limit, len(rows))}）")
    for r in rows[:limit]:
        print("   " + " | ".join(f"{k}={r[k]}" for k in r.keys() if r[k] not in (None, ""))[:260])
    con.close()
    return rows


print(f"库文件: {DB} ({os.path.getsize(DB)/1024/1024:.1f} MB)")

show("① 某单位的许可证状态与变更记录（含时间/摘要/链接）",
     "SELECT 单位名称, 证字号, 证件类型, 状态, 状态依据, 有效期止, 变更次数, 最近变更日期, 最近变更摘要, 官网链接 "
     "FROM view_license_current WHERE 单位名称 LIKE ? ORDER BY 变更次数 DESC", ("%中核苏阀%",))

show("①b 该许可证的逐条变更记录（含 diff 明细规模与附件页码）",
     "SELECT c.变更日期, c.变更摘要, c.附件页码, c.官网链接, length(c.变更明细) AS 明细JSON长度 "
     "FROM changes c JOIN licenses l ON l.license_id=c.license_id "
     "WHERE l.单位名称 LIKE ? ORDER BY c.变更日期 DESC", ("%中核苏阀%",))

show("② 按设备类别+品种查最新制造能力特征参数（设备粒度）",
     "SELECT 设备类别, 设备品种, 核安全级别, 能力特征参数, 持证单位, 证字号, 许可证状态, 官网链接, 附件页码 "
     "FROM view_device_index WHERE 设备类别=? AND 设备品种 LIKE ?", ("阀门", "%隔离阀%"))

show("②b 按设备品种聚合：哪些单位有该设备的最新能力",
     "SELECT 设备品种, COUNT(*) AS 许可证数, COUNT(DISTINCT 持证单位) AS 单位数 "
     "FROM view_device_index WHERE 设备类别=? GROUP BY 设备品种 ORDER BY 许可证数 DESC", ("阀门",), limit=12)

show("③ 按持证单位 → 证字号 + 变更编号 + 原文链接 + 附件页码",
     "SELECT l.单位名称, l.证字号, l.证件类型, l.状态, c.change_id AS 变更编号, c.变更日期, "
     "c.官网链接, c.附件页码 FROM licenses l LEFT JOIN changes c ON c.license_id=l.license_id "
     "WHERE l.单位名称 LIKE ? ORDER BY c.变更日期", ("%江苏翔淳%",))

show("③b 状态分布总览（有效/无效）",
     "SELECT 状态, COUNT(*) AS 许可证数 FROM licenses GROUP BY 状态 ORDER BY 许可证数 DESC")

show("③c 无官网链接/无附件页码的记录数（可追溯性检查）",
     "SELECT SUM(官网链接 IS NULL) AS 缺链接, SUM(附件页码 IS NULL) AS 缺页码, COUNT(*) AS 总数 FROM licenses")

show("④ 境外注册登记：按国别与设备类别检索",
     "SELECT 单位所在国, 单位名称, 设备类别, 设备品种, 核安全级别, 活动类别, 源页码 "
     "FROM registry_scopes WHERE 单位所在国=? LIMIT 6", ("美国",))
