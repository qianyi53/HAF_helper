# -*- coding: utf-8 -*-
"""tests/test_pdf_parser.py — L3 分块 / 确定性抽取单元测试（夹具为真实公文裁剪）

运行: python -X utf8 -m unittest tests.test_pdf_parser -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from haf import pdf_parser as P  # noqa: E402

FX = os.path.join(ROOT, "tests", "fixtures")


def fx(name):
    return os.path.join(FX, name)


def parse(name, **ctx):
    context = {"att_name": name}
    context.update(ctx)
    return P.parse_pdf(fx(name), ctx=context)


class TestPureHelpers(unittest.TestCase):
    def test_parse_value_num_with_limit(self):
        self.assertEqual(P.parse_value("≤1050", "DN"), {"值": 1050, "上限": True, "单位": "DN"})

    def test_parse_value_with_extra_condition(self):
        v = P.parse_value("≤5030（事故工况下≤8675）", "kN")
        self.assertEqual(v["值"], 5030)
        self.assertTrue(v["上限"])
        self.assertEqual(v["附加"]["事故工况"]["值"], 8675)

    def test_parse_value_slash_is_none(self):
        self.assertIsNone(P.parse_value("/"))

    def test_parse_value_text_keeps_raw(self):
        self.assertEqual(P.parse_value("液压"), {"原文": "液压"})

    def test_parse_cert_no_normalizes(self):
        self.assertEqual(P.parse_cert_no("（国核安证字 S（26）16 号）"), "国核安证字S（26）16号")

    def test_parse_cn_date(self):
        self.assertEqual(P.parse_cn_date("2026 年 9 月 30 日"), "2026-09-30")

    def test_clean_cell_removes_cjk_wrap_spaces_keeps_latin(self):
        self.assertEqual(P.clean_cell("高温气冷\n堆核安全\n机械设备"), "高温气冷堆核安全机械设备")
        self.assertEqual(P.clean_cell("Emerson Automation\nSolutions LP"), "Emerson Automation Solutions LP")

    def test_split_multi(self):
        self.assertEqual(P.split_multi("设计、制造"), ["设计", "制造"])

    def test_extract_subcontract_keeps_leading_number(self):
        r = P.extract_subcontract("分包项目：80吨以上吊装设备租赁。")
        self.assertEqual(r, ["80吨以上吊装设备租赁"])

    def test_extract_subcontract_numbered_list(self):
        r = P.extract_subcontract("主要分包项目：1.高温拉伸试验；2.表面处理；3.热处理。")
        self.assertEqual(r, ["高温拉伸试验", "表面处理", "热处理"])

    def test_validity_from_year_rule(self):
        txt, end = P.extract_validity("许可证有效期 5 年。", "2026-09-01")
        self.assertIn("有效期5年", txt)
        self.assertEqual(end, "2031-08-31")

    def test_validity_until_date(self):
        txt, end = P.extract_validity("许可证有效期至 2031 年 9 月 30 日。")
        self.assertEqual(end, "2031-09-30")


class TestLicenseSingle(unittest.TestCase):
    """证书页 + 附表（设计类，无能力参数列）"""

    @classmethod
    def setUpClass(cls):
        cls.recs = parse("license_design.pdf", doc_date="2026-09-01")
        cls.rec = cls.recs[0] if cls.recs else None

    def test_one_block_as_license(self):
        self.assertEqual(len(self.recs), 1)
        self.assertEqual(self.rec["kind"], "license")
        self.assertEqual(self.rec["cert_kind"], "设计")
        self.assertEqual((self.rec["page_start"], self.rec["page_end"]), (1, 3))

    def test_cert_fields(self):
        self.assertEqual(self.rec["cert_no"], "国核安证字S（26）16号")
        self.assertEqual(self.rec["unit_name"], "中核能源科技有限公司")
        f = self.rec["fields"]
        self.assertEqual(f["法定代表人"], "王志刚")
        self.assertIn("压力容器", f["设备类别"])
        self.assertIn("阀门", f["设备类别"])

    def test_no_sentence_leak_into_fields(self):
        for v in self.rec["fields"]["核安全级别"]:
            self.assertNotIn("国家核安全局", v)

    def test_scope_rows(self):
        rows = self.rec["scope_items"]
        self.assertEqual(len(rows), 14)
        self.assertEqual(rows[0]["设备类别"], "压力容器")
        self.assertEqual(rows[0]["设备种类"], "高温气冷堆核安全机械设备")
        self.assertTrue(rows[0]["活动范围及完成形式"])
        self.assertEqual(rows[0]["源页"], 3)

    def test_event_is_issue(self):
        self.assertEqual(self.rec["event"], "颁发")

    def test_validity_captured_from_condition_page(self):
        self.assertIn("有效期", self.rec["fields"]["有效期原文"] or "")


class TestLicenseMulti(unittest.TestCase):
    """多证合一（一证一块）"""

    @classmethod
    def setUpClass(cls):
        cls.recs = parse("license_multi.pdf", doc_date="2026-09-01")

    def test_two_blocks(self):
        self.assertEqual(len(self.recs), 2)
        self.assertEqual([r["cert_kind"] for r in self.recs], ["设计", "制造"])

    def test_units_and_cert_numbers(self):
        self.assertEqual(self.recs[0]["unit_name"], "中核苏阀科技实业股份有限公司")
        self.assertEqual(self.recs[0]["cert_no"], "国核安证字S（26）17号")
        self.assertEqual(self.recs[1]["cert_no"], "国核安证字Z（26）31号")
        self.assertEqual(self.recs[0]["page_start"], 1)
        self.assertEqual(self.recs[1]["page_start"], 5)

    def test_scope_rows_have_capability_params(self):
        rows = self.recs[1]["scope_items"]
        self.assertTrue(rows)
        cap = rows[0]["能力特征参数"]
        self.assertIn("公称通径", cap)
        self.assertEqual(cap["公称通径"]["值"], 1050)
        self.assertTrue(cap["公称通径"]["上限"])
        self.assertEqual(cap["公称通径"]["单位"], "DN")


class TestChangeSingle(unittest.TestCase):
    """变更范围表（单页单证，含能力特征参数与非标备注）"""

    @classmethod
    def setUpClass(cls):
        cls.recs = parse("change_scope.pdf", att_name="常州格林电力机械制造有限公司民用核安全设备制造许可活动范围表.pdf")
        cls.rec = cls.recs[0]

    def test_block_kind_and_unit(self):
        self.assertEqual(len(self.recs), 1)
        self.assertEqual(self.rec["kind"], "scope_only")
        self.assertEqual(self.rec["cert_kind"], "制造")
        self.assertEqual(self.rec["unit_name"], "常州格林电力机械制造有限公司")

    def test_rows_and_params(self):
        rows = self.rec["scope_items"]
        self.assertEqual(len(rows), 3)
        cap = rows[0]["能力特征参数"]
        self.assertEqual(cap["结构形式"], {"原文": "液压"})
        self.assertEqual(cap["载荷等级"]["值"], 5030)
        self.assertEqual(cap["载荷等级"]["附加"]["事故工况"]["值"], 8675)
        self.assertIn("装配", rows[0]["主要关键工艺"])

    def test_subcontract_parsed(self):
        self.assertTrue(any(s["分包项目"] for s in self.rec["scope_items"]))


class TestChangeMulti(unittest.TestCase):
    """变更多单位：一页一家"""

    @classmethod
    def setUpClass(cls):
        cls.recs = parse("change_multi.pdf")

    def test_one_unit_per_page(self):
        self.assertEqual(len(self.recs), 3)
        for i, r in enumerate(self.recs, 1):
            self.assertEqual(r["kind"], "scope_only")
            self.assertEqual(r["page_start"], i)
            self.assertEqual(r["page_start"], r["page_end"])
            self.assertTrue(r["unit_name"])

    def test_units_are_distinct(self):
        names = [r["unit_name"] for r in self.recs]
        self.assertEqual(len(set(names)), 3)

    def test_capability_from_plain_columns(self):
        """铸锻件类：材料类别 / 成品重量 等"单行表头"参数列也要进 JSON"""
        cap = self.recs[0]["scope_items"][0]["能力特征参数"]
        self.assertTrue(cap, "应至少解析出一个参数")


class TestDesignParams(unittest.TestCase):
    """设计能力特征参数（两行表头：公称通径/设计压力/设计温度）"""

    @classmethod
    def setUpClass(cls):
        cls.recs = parse("change_design_params.pdf")

    def test_rows(self):
        self.assertEqual(len(self.recs), 1)
        rows = self.recs[0]["scope_items"]
        self.assertGreaterEqual(len(rows), 15)
        cap = rows[0]["能力特征参数"]
        self.assertEqual(cap["设计压力"]["值"], 8.9)
        self.assertEqual(cap["设计温度"]["单位"], "℃")

    def test_header_not_treated_as_data(self):
        for r in self.recs[0]["scope_items"]:
            self.assertNotIn("设计温度（℃）", str(r["能力特征参数"].keys()))


class TestOverseas(unittest.TestCase):
    def test_single_unit(self):
        recs = parse("overseas_single.pdf")
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r["kind"], "overseas")
        self.assertIn("Emerson", r["unit_name"])
        self.assertTrue(r["scope_items"])
        acts = {s.get("活动类别") for s in r["scope_items"]}
        self.assertIn("设计", acts)
        self.assertIn("制造", acts)

    def test_multi_unit_blocks(self):
        recs = parse("overseas_multi.pdf")
        self.assertGreaterEqual(len(recs), 2)
        self.assertTrue(all(r["kind"] == "overseas" for r in recs))
        self.assertTrue(all(r["unit_name"] for r in recs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
