# -*- coding: utf-8 -*-
"""haf/db_builder.py — L4 建库：SQLite 单文件（6 表 + 2 视图 + 物理外键）

裁定：Q-L4-1 —— workbuddy database 可导入 SQLite，交付形态为 SQLite 单文件。
用法:
  python -X utf8 -m haf.db_builder --in data/parsed_records.json --out data/db/haf.db
  python -X utf8 -m haf.db_builder --csv          # 额外导出 CSV 分表（便于人工核验）
"""
import argparse
import csv
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_IN = os.path.join(ROOT, "data", "parsed_records.json")
DEFAULT_OUT = os.path.join(ROOT, "data", "db", "haf.db")

DDL = """
-- 1. 文函主表（L1/L2 透传）
CREATE TABLE IF NOT EXISTS documents (
    doc_id       TEXT PRIMARY KEY,
    标题         TEXT,
    文号         TEXT,
    分类         TEXT,
    发布机关     TEXT,
    生成日期     TEXT,
    成文日期     TEXT,
    事件标签     TEXT,
    官网链接     TEXT,
    正文         TEXT,
    附件数       INTEGER,
    已解析       INTEGER DEFAULT 0,
    官网状态     TEXT,                  -- 如 missing_in_list（官网列表已不见，疑似撤稿）
    爬取时间     TEXT
);

-- 2. 许可证（一证一条，含状态）
CREATE TABLE IF NOT EXISTS licenses (
    license_id    TEXT PRIMARY KEY,
    单位名称      TEXT NOT NULL,
    单位所在国    TEXT,
    证字号        TEXT,
    证件类型      TEXT NOT NULL,
    活动种类      TEXT,                 -- JSON 数组
    状态          TEXT,                 -- 有效/无效/已注销/待核实
    状态依据      TEXT,
    发证日期      TEXT,
    有效期起      TEXT,
    有效期止      TEXT,
    有效期原文    TEXT,
    设备类别汇总  TEXT,                 -- JSON 数组
    核安全级别汇总 TEXT,                -- JSON 数组
    当前版本id    TEXT,
    版本数        INTEGER,
    数据标志      TEXT,                 -- 如"来源文函官网已撤下"
    官网链接      TEXT,
    附件链接      TEXT,
    附件页码      TEXT,
    更新日期      TEXT,
    FOREIGN KEY (当前版本id) REFERENCES license_versions(version_id)
);
CREATE INDEX IF NOT EXISTS idx_lic_unit   ON licenses(单位名称);
CREATE INDEX IF NOT EXISTS idx_lic_status ON licenses(状态);
CREATE INDEX IF NOT EXISTS idx_lic_type   ON licenses(证件类型);

-- 3. 许可证版本（每次颁发/延续/变更一版）
CREATE TABLE IF NOT EXISTS license_versions (
    version_id   TEXT PRIMARY KEY,
    license_id   TEXT NOT NULL,
    单位名称     TEXT,
    证件类型     TEXT,
    来源事件     TEXT,                  -- 颁发/延续/不予延续/变更
    来源doc_id   TEXT,
    生效日期     TEXT,
    证字号       TEXT,                  -- 版本属性（换证会变）
    附件链接     TEXT,
    页码起       INTEGER,
    页码止       INTEGER,
    范围行数     INTEGER,
    抽取模式     TEXT,                  -- rule / llm(补全) / llm
    文档状态     TEXT,                  -- 如"官网未见"
    change_id    TEXT,
    FOREIGN KEY (license_id) REFERENCES licenses(license_id),
    FOREIGN KEY (来源doc_id) REFERENCES documents(doc_id),
    FOREIGN KEY (change_id) REFERENCES changes(change_id)
);
CREATE INDEX IF NOT EXISTS idx_ver_lic  ON license_versions(license_id);
CREATE INDEX IF NOT EXISTS idx_ver_doc  ON license_versions(来源doc_id);

-- 4. 许可活动范围明细（设备粒度核心表）
CREATE TABLE IF NOT EXISTS scope_items (
    scope_item_id       TEXT PRIMARY KEY,
    version_id          TEXT NOT NULL,
    license_id          TEXT NOT NULL,
    设备种类            TEXT,
    设备类别            TEXT,
    设备品种            TEXT,
    民用核设施类别      TEXT,
    活动类别            TEXT,
    核安全级别          TEXT,
    能力特征参数        TEXT,            -- JSON（大类套小类）
    能力特征参数可读    TEXT,            -- 人读串
    主要关键工艺        TEXT,            -- JSON 数组
    活动范围及完成形式  TEXT,
    活动场所            TEXT,
    备注                TEXT,
    分包项目            TEXT,            -- JSON 数组
    源页码              INTEGER,
    FOREIGN KEY (version_id) REFERENCES license_versions(version_id),
    FOREIGN KEY (license_id) REFERENCES licenses(license_id)
);
CREATE INDEX IF NOT EXISTS idx_scope_cat  ON scope_items(设备类别);
CREATE INDEX IF NOT EXISTS idx_scope_var  ON scope_items(设备品种);
CREATE INDEX IF NOT EXISTS idx_scope_ver  ON scope_items(version_id);

-- 5. 变更记录
CREATE TABLE IF NOT EXISTS changes (
    change_id    TEXT PRIMARY KEY,
    license_id   TEXT,
    单位名称     TEXT,
    证件类型     TEXT,
    证字号       TEXT,
    变更日期     TEXT,
    变更类型     TEXT,
    变更摘要     TEXT,
    变更明细     TEXT,                  -- JSON: 新增/删除/修改
    来源doc_id   TEXT,
    官网链接     TEXT,
    附件页码     TEXT,
    抽取模式     TEXT,
    diff基础版本 TEXT,
    FOREIGN KEY (license_id) REFERENCES licenses(license_id),
    FOREIGN KEY (来源doc_id) REFERENCES documents(doc_id)
);
CREATE INDEX IF NOT EXISTS idx_chg_lic ON changes(license_id);

-- 6. 境外单位注册登记范围
CREATE TABLE IF NOT EXISTS registry_scopes (
    registry_scope_id TEXT PRIMARY KEY,
    version_id        TEXT NOT NULL,
    license_id        TEXT NOT NULL,
    单位名称          TEXT,
    单位所在国        TEXT,
    活动类别          TEXT,
    设备类别          TEXT,
    设备品种          TEXT,
    核安全级别        TEXT,
    备注              TEXT,
    源页码            INTEGER,
    FOREIGN KEY (version_id) REFERENCES license_versions(version_id),
    FOREIGN KEY (license_id) REFERENCES licenses(license_id)
);
CREATE INDEX IF NOT EXISTS idx_reg_cat ON registry_scopes(设备类别);

-- ============ 视图（可读主数据） ============
DROP VIEW IF EXISTS view_license_current;
CREATE VIEW view_license_current AS
SELECT
    l.单位名称            AS 单位名称,
    l.单位所在国          AS 单位所在国,
    l.证字号              AS 证字号,
    l.证件类型            AS 证件类型,
    REPLACE(REPLACE(IFNULL(l.活动种类,'[]'), '["', ''), '"]', '') AS 活动种类,
    l.状态                AS 状态,
    l.状态依据            AS 状态依据,
    l.有效期起            AS 有效期起,
    l.有效期止            AS 有效期止,
    REPLACE(REPLACE(IFNULL(l.设备类别汇总,'[]'), '["', ''), '"]', '') AS 设备类别汇总,
    REPLACE(REPLACE(IFNULL(l.核安全级别汇总,'[]'), '["', ''), '"]', '') AS 核安全级别汇总,
    l.版本数              AS 版本数,
    (SELECT COUNT(*) FROM changes c WHERE c.license_id = l.license_id) AS 变更次数,
    (SELECT c.变更日期 FROM changes c WHERE c.license_id = l.license_id ORDER BY c.变更日期 DESC LIMIT 1) AS 最近变更日期,
    (SELECT c.变更摘要 FROM changes c WHERE c.license_id = l.license_id ORDER BY c.变更日期 DESC LIMIT 1) AS 最近变更摘要,
    (SELECT COUNT(*) FROM scope_items s WHERE s.license_id = l.license_id) AS 范围行数,
    l.数据标志            AS 数据标志,
    l.官网链接            AS 官网链接,
    l.附件链接            AS 附件链接,
    l.附件页码            AS 附件页码
FROM licenses l;

DROP VIEW IF EXISTS view_device_index;
CREATE VIEW view_device_index AS
SELECT
    s.设备类别              AS 设备类别,
    s.设备品种              AS 设备品种,
    s.核安全级别            AS 核安全级别,
    s.能力特征参数可读      AS 能力特征参数,
    s.能力特征参数          AS 能力特征参数JSON,
    s.主要关键工艺          AS 主要关键工艺,
    l.单位名称              AS 持证单位,
    l.证字号                AS 证字号,
    l.证件类型              AS 证件类型,
    l.状态                  AS 许可证状态,
    v.来源事件              AS 数据来源事件,
    v.生效日期              AS 数据生效日期,
    v.文档状态              AS 来源文函状态,
    l.官网链接              AS 官网链接,
    l.附件链接              AS 附件链接,
    s.源页码                AS 附件页码
FROM scope_items s
JOIN licenses l         ON l.license_id = s.license_id
JOIN license_versions v ON v.version_id = s.version_id
WHERE v.version_id = l.当前版本id;
"""

TABLE_COLUMNS = {
    "documents": ["doc_id", "标题", "文号", "分类", "发布机关", "生成日期", "成文日期",
                  "事件标签", "官网链接", "正文", "附件数", "已解析", "官网状态", "爬取时间"],
    "licenses": ["license_id", "单位名称", "单位所在国", "证字号", "证件类型", "活动种类",
                 "状态", "状态依据", "发证日期", "有效期起", "有效期止", "有效期原文",
                 "设备类别汇总", "核安全级别汇总", "当前版本id", "版本数", "数据标志",
                 "官网链接", "附件链接", "附件页码", "更新日期"],
    "license_versions": ["version_id", "license_id", "单位名称", "证件类型", "来源事件",
                         "来源doc_id", "生效日期", "证字号", "附件链接", "页码起", "页码止",
                         "范围行数", "抽取模式", "文档状态", "change_id"],
    "scope_items": ["scope_item_id", "version_id", "license_id", "设备种类", "设备类别",
                    "设备品种", "民用核设施类别", "活动类别", "核安全级别", "能力特征参数",
                    "能力特征参数可读", "主要关键工艺", "活动范围及完成形式", "活动场所",
                    "备注", "分包项目", "源页码"],
    "changes": ["change_id", "license_id", "单位名称", "证件类型", "证字号", "变更日期",
                "变更类型", "变更摘要", "变更明细", "来源doc_id", "官网链接", "附件页码",
                "抽取模式", "diff基础版本"],
    "registry_scopes": ["registry_scope_id", "version_id", "license_id", "单位名称",
                        "单位所在国", "活动类别", "设备类别", "设备品种", "核安全级别",
                        "备注", "源页码"],
}


def jsonify(v):
    """dict/list → JSON 字符串；其余原样（供 SQLite 存储）"""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return v


def build(model, docs, out_path, also_csv=False):
    d = os.path.dirname(out_path)
    if d:
        os.makedirs(d, exist_ok=True)
    if os.path.exists(out_path):
        os.remove(out_path)
    con = sqlite3.connect(out_path)
    # 建库阶段临时关闭外键（licenses ↔ license_versions 互为引用，插入顺序无法同时满足），
    # 全部写入并 commit 后再打开外键并做 PRAGMA foreign_key_check 校验
    con.execute("PRAGMA foreign_keys = OFF")
    con.executescript(DDL)

    # 插入顺序需满足外键：documents → licenses → license_versions → scope/changes/registry
    con.executemany(
        f"INSERT INTO documents ({','.join(TABLE_COLUMNS['documents'])}) "
        f"VALUES ({','.join('?' * len(TABLE_COLUMNS['documents']))})",
        [[jsonify(doc.get(c)) for c in TABLE_COLUMNS["documents"]] for doc in docs])

    for table in ("licenses", "license_versions", "scope_items", "changes", "registry_scopes"):
        cols = TABLE_COLUMNS[table]
        rows = [[jsonify(r.get(c)) for c in cols] for r in model.get(table, [])]
        con.executemany(f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) "
                        f"VALUES ({','.join('?' * len(cols))})", rows)
    con.commit()
    con.execute("PRAGMA foreign_keys = ON")
    fk_errors = con.execute("PRAGMA foreign_key_check").fetchall()
    con.execute("VACUUM")
    con.commit()

    stats = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
             for t in TABLE_COLUMNS}
    stats["view_license_current"] = con.execute("SELECT COUNT(*) FROM view_license_current").fetchone()[0]
    stats["view_device_index"] = con.execute("SELECT COUNT(*) FROM view_device_index").fetchone()[0]
    con.close()

    if also_csv:
        csv_dir = os.path.join(os.path.dirname(out_path), "csv")
        export_csv(model, csv_dir, docs=docs)

    return stats, fk_errors


def export_csv(model, out_dir, docs=None):
    """导出 CSV 分表（documents 表数据由调用方传入，与 SQLite 写入保持一致）"""
    os.makedirs(out_dir, exist_ok=True)
    for table, cols in TABLE_COLUMNS.items():
        rows = docs if (table == "documents" and docs is not None) else model.get(table, [])
        path = os.path.join(out_dir, f"{table}.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for r in rows:
                w.writerow([jsonify(r.get(c)) if not isinstance(r.get(c), (dict, list))
                            else json.dumps(r.get(c), ensure_ascii=False) for c in cols])
    return out_dir


def main():
    ap = argparse.ArgumentParser(description="L4 建库（SQLite）")
    ap.add_argument("--in", dest="in_path", default=DEFAULT_IN)
    ap.add_argument("--out", dest="out_path", default=DEFAULT_OUT)
    ap.add_argument("--csv", action="store_true", help="额外导出 CSV 分表")
    args = ap.parse_args()

    data = json.load(open(args.in_path, encoding="utf-8"))
    docs = data.get("documents") or []
    model = {k: data.get(k, []) for k in TABLE_COLUMNS}
    stats, fk_errors = build(model, docs, args.out_path, also_csv=args.csv)
    print(f"[i] 输入: {args.in_path}")
    for k, v in stats.items():
        print(f"    {k:22s} {v}")
    if fk_errors:
        print(f"[!] 外键校验发现 {len(fk_errors)} 处问题: {fk_errors[:5]}")
    else:
        print("[OK] 外键校验通过")
    print(f"[i] 输出: {args.out_path}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    main()
