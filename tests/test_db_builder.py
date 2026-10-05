# -*- coding: utf-8 -*-
"""tests/test_db_builder.py — L4 SQLite 建库（表/视图/外键/可读性）单元测试"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from haf import chain, db_builder  # noqa: E402


def rec(doc_id, unit, event, date, cert_no=None, rows=None, kind="license", cert_kind="制造"):
    return {
        "kind": kind, "cert_kind": cert_kind, "cert_no": cert_no, "unit_name": unit,
        "event": event,
        "fields": {"设备类别": ["阀门"], "核安全级别": ["核安全1级"],
                   "有效期止": "2031-09-30", "有效期原文": "许可证有效期至2031年9月30日"},
        "活动种类": ["制造"], "scope_items": rows or [],
        "page_start": 1, "page_end": 3, "doc_id": doc_id, "doc_date": date,
        "att_name": "a.pdf", "att_url": "http://x/a.pdf", "extract_mode": "rule",
    }


def row(cat, var, level, params=None, place="厂址A"):
    return {"设备类别": cat, "设备品种": var, "核安全级别": level,
            "能力特征参数": params or {"公称通径": {"值": 1050, "上限": True, "单位": "DN"}},
            "主要关键工艺": ["焊接"], "活动范围及完成形式": "按图纸制造",
            "活动场所": place, "备注": None, "分包项目": [], "源页": 3}


DOCS = [
    {"doc_id": "d0", "标题": "关于颁发…", "官网链接": "http://n/d0", "生成日期": "2026-01-05",
     "成文日期": "2026-01-05", "事件标签": "颁发", "附件数": 1, "已解析": 1},
    {"doc_id": "d1", "标题": "关于批准变更…", "官网链接": "http://n/d1", "生成日期": "2026-05-18",
     "成文日期": "2026-05-15", "事件标签": "变更", "附件数": 1, "已解析": 1},
]


def build_model():
    recs = [
        rec("d0", "某某阀门有限公司", "颁发", "2026-01-05", "国核安证字Z（26）10号",
            [row("阀门", "闸阀", "1级")]),
        rec("d1", "某某阀门有限公司", "变更", "2026-05-15", None,
            [row("阀门", "闸阀", "1级"), row("阀门", "截止阀", "1级")], kind="scope_only"),
        rec("d0", "Emerson Automation Solutions LP", "颁发", "2026-09-10", None,
            [{"设备类别": "阀门", "设备品种": "安全阀", "核安全级别": "1级", "能力特征参数": {},
              "活动类别": "设计", "备注": None, "源页": 1}],
            kind="overseas", cert_kind="境外注册登记"),
    ]
    return chain.build_model(recs, docs_index={d["doc_id"]: d for d in DOCS})


class TestDbBuilder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.db = os.path.join(cls.tmp, "haf.db")
        cls.model = build_model()
        cls.stats, cls.fk_errors = db_builder.build(cls.model, DOCS, cls.db)

    def q(self, sql, args=()):
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()

    def test_fk_integrity(self):
        self.assertEqual(self.fk_errors, [], f"外键校验异常: {self.fk_errors}")

    def test_tables_and_views_exist(self):
        names = {r["name"] for r in self.q(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        for t in ("documents", "licenses", "license_versions", "scope_items",
                  "changes", "registry_scopes", "view_license_current", "view_device_index"):
            self.assertIn(t, names)

    def test_counts(self):
        self.assertEqual(self.stats["documents"], 2)
        self.assertEqual(self.stats["licenses"], 2)
        self.assertEqual(self.stats["license_versions"], 3)
        self.assertEqual(self.stats["changes"], 1)
        self.assertEqual(self.stats["registry_scopes"], 1)

    def test_view_license_current_readable(self):
        rows = self.q("SELECT * FROM view_license_current")
        cols = rows[0].keys()
        for c in ("单位名称", "证字号", "证件类型", "状态", "设备类别汇总", "变更次数",
                  "最近变更日期", "官网链接", "附件页码"):
            self.assertIn(c, cols)
        lic = [r for r in rows if r["单位名称"] == "某某阀门有限公司"][0]
        self.assertEqual(lic["状态"], "有效")
        self.assertEqual(lic["变更次数"], 1)
        self.assertIn("阀门", lic["设备类别汇总"])

    def test_view_device_index_uses_latest_version_only(self):
        rows = self.q("SELECT * FROM view_device_index WHERE 持证单位 = ?", ("某某阀门有限公司",))
        # 最新版本(变更后)有 2 行；旧版本只有 1 行，不应出现
        self.assertEqual(len(rows), 2)
        vars_ = {r["设备品种"] for r in rows}
        self.assertEqual(vars_, {"闸阀", "截止阀"})
        self.assertTrue(rows[0]["能力特征参数"])
        self.assertEqual(rows[0]["附件页码"], 3)

    def test_changes_stored_with_detail_json(self):
        r = self.q("SELECT * FROM changes")[0]
        detail = json.loads(r["变更明细"])
        self.assertIn("新增", detail)
        self.assertIn("修改", detail)

    def test_view_device_index_by_device_query(self):
        """按设备品种检索 → 定位证字号与链接（用户诉求③）"""
        rows = self.q("SELECT 证字号, 官网链接 FROM view_device_index WHERE 设备类别 = '阀门'")
        self.assertTrue(rows)
        self.assertTrue(all(r["官网链接"] for r in rows))

    def test_csv_export(self):
        out = os.path.join(self.tmp, "csv")
        db_builder.export_csv(self.model, out, docs=DOCS)
        files = set(os.listdir(out))
        self.assertIn("licenses.csv", files)
        self.assertIn("scope_items.csv", files)
        with open(os.path.join(out, "documents.csv"), encoding="utf-8-sig") as f:
            lines = f.read().strip().splitlines()
        self.assertEqual(len(lines), len(DOCS) + 1)      # 表头 + 文函行


if __name__ == "__main__":
    unittest.main(verbosity=2)
