# -*- coding: utf-8 -*-
"""tests/test_chain.py — 版本链 / 变更 diff / 状态判定 单元测试（构造数据）"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from haf import chain  # noqa: E402


def rec(doc_id, unit, cert_kind, event, date, cert_no=None, rows=None, kind="license", **fields):
    f = {"设备类别": ["阀门"], "核安全级别": ["核安全1级"], "有效期原文": None, "有效期止": None}
    f.update(fields)
    return {
        "kind": kind, "cert_kind": cert_kind, "cert_no": cert_no, "unit_name": unit,
        "event": event, "活动种类": [], "fields": f, "scope_items": rows or [],
        "page_start": 1, "page_end": 3, "doc_id": doc_id, "doc_date": date,
        "att_name": f"{unit}.pdf", "att_url": f"http://x/{doc_id}.pdf", "extract_mode": "rule",
    }


def row(cat, var, level, params=None, place="厂址A", remark=None):
    return {"设备类别": cat, "设备品种": var, "核安全级别": level, "能力特征参数": params or {},
            "主要关键工艺": [], "活动范围及完成形式": "按图纸制造", "活动场所": place,
            "备注": remark, "分包项目": [], "源页": 3}


DOCS = {
    "d1": {"官网链接": "http://n/d1", "事件标签": ["颁发"]},
    "d2": {"官网链接": "http://n/d2", "事件标签": ["变更"]},
    "d3": {"官网链接": "http://n/d3", "事件标签": ["不予延续"]},
}


class TestVersionChain(unittest.TestCase):
    def test_renew_changes_cert_no_but_keeps_license_identity(self):
        """延续换证：证字号变化，但仍是同一许可证（主键稳定）"""
        bad = rec("d0", "某某阀门有限公司", "制造", "颁发", "2021-05-01", "国核安证字Z（21）10号",
                  [row("阀门", "闸阀", "1级")])
        r1 = rec("d1", "某某阀门有限公司", "制造", "延续", "2026-06-01", "国核安证字Z（26）90号",
                 [row("阀门", "闸阀", "1级"), row("阀门", "截止阀", "1级")])
        m = chain.build_model([bad, r1], docs_index=DOCS)
        self.assertEqual(len(m["licenses"]), 1)
        lic = m["licenses"][0]
        self.assertEqual(lic["证字号"], "国核安证字Z（26）90号")   # 取最新
        self.assertEqual(lic["版本数"], 2)
        self.assertEqual(lic["状态"], "有效")
        self.assertEqual(lic["官网链接"], "http://n/d1")

    def test_scope_only_change_creates_version_and_diff(self):
        """变更范围表（无证字号）→ 变更版本 + diff 明细 + 摘要"""
        issue = rec("d0", "常州格林电力机械制造有限公司", "制造", "颁发", "2026-01-10",
                    "国核安证字Z（26）15号", [row("支承件", "阻尼器", "1级", {"载荷等级": {"值": 5000}})])
        chg = rec("d1", "常州格林电力机械制造有限公司", "制造", "变更", "2026-05-15", None,
                  [row("支承件", "阻尼器", "1级", {"载荷等级": {"值": 5030, "上限": True}}),
                   row("支承件", "管道支承件", "2级")], kind="scope_only")
        m = chain.build_model([issue, chg], docs_index=DOCS)
        self.assertEqual(len(m["changes"]), 1)
        c = m["changes"][0]
        self.assertTrue(c["change_id"].startswith("Cd1-"), c["change_id"])
        self.assertEqual(c["变更类型"], "许可活动范围")
        self.assertIn("新增 1 项", c["变更摘要"])
        self.assertEqual(len(c["变更明细"]["新增"]), 1)
        self.assertEqual(len(c["变更明细"]["修改"]), 1)
        # 变更版本继承上一版证字号
        ver = [v for v in m["license_versions"] if v["来源事件"] == "变更"][0]
        self.assertEqual(ver["证字号"], "国核安证字Z（26）15号")
        self.assertTrue(ver["change_id"].startswith("Cd1-"), ver["change_id"])

    def test_no_diff_when_rows_identical(self):
        issue = rec("d0", "甲有限公司", "制造", "颁发", "2026-01-10", "国核安证字Z（26）01号",
                    [row("阀门", "闸阀", "1级")])
        chg = rec("d1", "甲有限公司", "制造", "变更", "2026-03-01", None,
                  [row("阀门", "闸阀", "1级")], kind="scope_only")
        m = chain.build_model([issue, chg], docs_index=DOCS)
        self.assertIn("一致", m["changes"][0]["变更摘要"])

    def test_diff_detects_removal(self):
        issue = rec("d0", "乙有限公司", "制造", "颁发", "2026-01-10", "Z01",
                    [row("阀门", "闸阀", "1级"), row("阀门", "截止阀", "1级")])
        chg = rec("d1", "乙有限公司", "制造", "变更", "2026-03-01", None,
                  [row("阀门", "闸阀", "1级")], kind="scope_only")
        m = chain.build_model([issue, chg], docs_index=DOCS)
        self.assertEqual(len(m["changes"][0]["变更明细"]["删除"]), 1)


class TestStatus(unittest.TestCase):
    def _one(self, **kw):
        return chain.build_model([rec("d1", "丙公司", "制造", **kw)], docs_index=DOCS)

    def test_valid_when_future_expiry(self):
        m = self._one(event="延续", date="2026-01-01", cert_no="Z1", rows=[row("阀门", "闸阀", "1级")],
                      fields_有效期止="2031-09-30")
        # 直接构造 fields 更直观
        r = rec("d1", "丙公司", "制造", "延续", "2026-01-01", "Z1", [row("阀门", "闸阀", "1级")])
        r["fields"]["有效期止"] = "2031-09-30"
        m = chain.build_model([r], docs_index=DOCS)
        self.assertEqual(m["licenses"][0]["状态"], "有效")

    def test_invalid_when_expired(self):
        r = rec("d1", "丙公司", "制造", "颁发", "2020-01-01", "Z1", [row("阀门", "闸阀", "1级")])
        r["fields"]["有效期止"] = "2021-01-01"
        m = chain.build_model([r], docs_index=DOCS)
        self.assertEqual(m["licenses"][0]["状态"], "无效")
        self.assertIn("有效期已过", m["licenses"][0]["状态依据"])

    def test_invalid_when_renew_denied(self):
        r = rec("d3", "丁公司", "制造", "不予延续", "2026-01-01", "Z2")
        m = chain.build_model([r], docs_index=DOCS)
        self.assertEqual(m["licenses"][0]["状态"], "无效")
        self.assertIn("不予延续", m["licenses"][0]["状态依据"])

    def test_registry_scope_rows_built_for_overseas(self):
        r = rec("d1", "Emerson Automation Solutions LP", "境外注册登记", "颁发", "2026-09-10",
                kind="overseas", rows=[{"设备类别": "阀门", "设备品种": "安全阀", "核安全级别": "1级",
                                       "活动类别": "设计", "备注": None, "源页": 1}])
        r["fields"]["单位所在国"] = "美国"
        m = chain.build_model([r], docs_index=DOCS)
        self.assertEqual(len(m["registry_scopes"]), 1)
        self.assertEqual(m["registry_scopes"][0]["活动类别"], "设计")
        self.assertEqual(m["licenses"][0]["单位所在国"], "美国")

    def test_unit_name_variants_grouped(self):
        """名称仅空白/括号差异 → 视为同一许可证"""
        a = rec("d0", "甲公司 （本部）", "制造", "颁发", "2026-01-01", "Z1", [row("阀门", "闸阀", "1级")])
        b = rec("d1", "甲公司(本部)", "制造", "变更", "2026-02-01", None,
                [row("阀门", "闸阀", "1级"), row("阀门", "截止阀", "1级")], kind="scope_only")
        m = chain.build_model([a, b], docs_index=DOCS)
        self.assertEqual(len(m["licenses"]), 1)


class TestCancelRefs(unittest.TestCase):
    """L2「注销」标注文档中的内嵌注销引用 → 状态=已注销（M3 报告 §4.1）"""

    def test_extract_cancel_refs(self):
        docs = [{
            "doc_id": "d1", "链接": "http://n/d1", "生成日期": "2026-09-18", "成文日期": "2026-09-17",
            "labels": ["事件:颁发", "事件:注销", "证件类型:许可证"],
            "正文": "现决定准予延续并颁发许可证。同时注销无锡市华尔泰机械制造有限公司持有的"
                    "民用核安全设备制造许可证〔国核安证字Z（18）21号〕。另有其他内容。",
        }]
        refs = chain.extract_cancel_refs(docs)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["被注销单位"], "无锡市华尔泰机械制造有限公司")
        self.assertEqual(refs[0]["来源doc_id"], "d1")

    def test_cancel_ref_sets_status(self):
        r = rec("d0", "无锡市华尔泰机械制造有限公司", "制造", "颁发", "2018-06-01", "Z21",
                [row("阀门", "闸阀", "1级")])
        refs = [{"被注销单位": "无锡市华尔泰机械制造有限公司", "来源doc_id": "d1",
                 "日期": "2026-09-17", "官网链接": "http://n/d1"}]
        m = chain.build_model([r], docs_index=DOCS, cancel_refs=refs)
        self.assertEqual(m["licenses"][0]["状态"], "已注销")
        self.assertIn("注销", m["licenses"][0]["状态依据"])

    def test_cancel_ref_older_than_version_ignored(self):
        r = rec("d0", "某某公司", "制造", "延续", "2026-09-17", "Z99", [row("阀门", "闸阀", "1级")])
        refs = [{"被注销单位": "某某公司", "来源doc_id": "dX", "日期": "2020-01-01"}]
        m = chain.build_model([r], docs_index=DOCS, cancel_refs=refs)
        self.assertNotEqual(m["licenses"][0]["状态"], "已注销")


class TestMissingDocSync(unittest.TestCase):
    """M6 撤稿同步：来源文函在官网列表消失 → 打数据标志并降级为待核实"""

    def test_missing_doc_marks_license(self):
        r = rec("d0", "某某公司", "制造", "颁发", "2026-01-01", "Z1", [row("阀门", "闸阀", "1级")])
        r["fields"]["有效期止"] = "2031-01-01"
        m = chain.build_model([r], docs_index=DOCS, missing_doc_ids={"d0"})
        lic = m["licenses"][0]
        self.assertEqual(lic["数据标志"], "来源文函官网已撤下")
        self.assertEqual(lic["状态"], "待核实")
        self.assertIn("官网列表已不见", lic["状态依据"])
        ver = m["license_versions"][0]
        self.assertEqual(ver["文档状态"], "官网未见")

    def test_normal_doc_has_no_flag(self):
        r = rec("d0", "某某公司", "制造", "颁发", "2026-01-01", "Z1", [row("阀门", "闸阀", "1级")])
        m = chain.build_model([r], docs_index=DOCS)
        self.assertIsNone(m["licenses"][0]["数据标志"])
        self.assertEqual(m["licenses"][0]["状态"], "有效")


class TestParamsReadable(unittest.TestCase):
    def test_design_params(self):
        s = chain.params_readable({"公称通径": {"值": 1050, "上限": True, "单位": "DN"},
                                   "设计压力": {"值": 8.9, "上限": True, "单位": "MPa"}})
        self.assertIn("DN≤1050", s)
        self.assertIn("P≤8.9MPa", s)

    def test_multi_value_list(self):
        s = chain.params_readable({"材料类别": ["不锈钢", "碳钢"]})
        self.assertIn("材料:不锈钢、碳钢", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
