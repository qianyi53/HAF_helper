# -*- coding: utf-8 -*-
"""
build_wechat.py — 从 haf.db 导出三个数据集，注入微信版单页模板

产出: wechat/核安全设备许可证查询.html
  - 数据内嵌（设备索引/许可证/变更 + 数据截止日），零外部依赖（无 SDK/CDN/外链图片）
  - 供资料库导入发布（workbuddy.link，微信可直接打开）

用法:
  python build_wechat.py [haf.db 路径]

数据口径与云端 view_device_index（重建版）一致:
  当前版本(链尾)的 scope_items JOIN licenses JOIN license_versions
"""
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)  # frontend/ 的上级 = 项目根
DB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(PROJECT_ROOT, "data", "db", "haf.db")
TEMPLATE = os.path.join(HERE, "wechat_template.html")
OUT_DIR = os.path.join(HERE, "wechat")
OUT = os.path.join(OUT_DIR, "核安全设备许可证查询.html")

PLACEHOLDER = "/*__HAF_DATA__*/null"


def rows(cur, sql):
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def main():
    con = sqlite3.connect(DB)
    cur = con.cursor()

    # ① 设备索引（当前版本范围条目，口径同云端 view_device_index 重建版）
    #    许可证级字段（持证单位/证字号/状态/链接等）不在此重复，由前端从 lic 合并，减小体积
    device = rows(cur, """
        SELECT
            s.license_id       AS license_id,
            s.设备种类         AS 设备种类,
            s.设备类别         AS 设备类别,
            s.设备品种         AS 设备品种,
            s.核安全级别       AS 核安全级别,
            s.能力特征参数可读 AS 能力特征参数,
            s.源页码           AS 附件页码
        FROM scope_items s
        JOIN licenses l         ON l.license_id = s.license_id
        JOIN license_versions v ON v.version_id = s.version_id
        WHERE v.version_id = l.当前版本id
        ORDER BY s.scope_item_id
    """)

    # ② 许可证（单位 Tab + 详情抽屉 + 设备索引合并源；数据生效日期取当前版本生效日期）
    lic = rows(cur, """
        SELECT l.license_id, l.单位名称, l.单位所在国, l.证件类型, l.证字号, l.活动种类,
               l.状态, l.状态依据, l.发证日期, l.有效期起, l.有效期止, l.有效期原文,
               l.版本数, l.数据标志, l.官网链接, l.附件链接, l.附件页码, l.更新日期,
               v.生效日期 AS 数据生效日期
        FROM licenses l
        LEFT JOIN license_versions v ON v.version_id = l.当前版本id
        ORDER BY l.单位名称
    """)

    # ③ 变更记录（变更明细 JSON 原样保留）
    chg = rows(cur, """
        SELECT change_id, license_id, 变更日期, 变更类型, 变更摘要, 变更明细,
               官网链接, 附件页码
        FROM changes
        ORDER BY 变更日期 DESC
    """)

    fresh = cur.execute("SELECT MAX(爬取时间) FROM documents").fetchone()[0]
    fresh = str(fresh)[:10] if fresh else ""
    con.close()

    data = {"fresh": fresh, "device": device, "lic": lic, "chg": chg}
    js = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # 内嵌 <script> 安全转义
    js = js.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")

    with open(TEMPLATE, "r", encoding="utf-8") as f:
        tpl = f.read()
    if PLACEHOLDER not in tpl:
        raise SystemExit("模板缺少占位符 " + PLACEHOLDER)
    html = tpl.replace(PLACEHOLDER, js, 1)

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)

    print("device=%d lic=%d chg=%d fresh=%s" % (len(device), len(lic), len(chg), fresh))
    print("data  : %.2f MB" % (len(js.encode("utf-8")) / 1048576))
    print("output: %s (%.2f MB)" % (OUT, os.path.getsize(OUT) / 1048576))


if __name__ == "__main__":
    main()
