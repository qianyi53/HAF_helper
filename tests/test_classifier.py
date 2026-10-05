# -*- coding: utf-8 -*-
"""tests/test_classifier.py — L2 打标单元测试

运行: python -m unittest tests.test_classifier -v
（在项目根目录执行；tests 仅为开发期资产，测试数据为从真实语料浓缩的最小样例）
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from haf.classifier import classify_doc  # noqa: E402


def mk(name, body="", atts=None):
    """构造最小文档样例"""
    return {"doc_id": "test", "名称": name, "正文": body, "附件及链接": atts or []}


class TestDomainGate(unittest.TestCase):
    """域门控: 域内命中 / 域外排除（裁定 Q-L2-1/Q-L2-2/Q-L2-3 的排除类别）"""

    def test_in_domain(self):
        labels, _, _ = classify_doc(mk("关于颁发××民用核安全设备制造许可证的通知"))
        self.assertTrue(labels)

    def test_out_nuclear_plant_license(self):
        """核电厂运行/建造许可证 → 排除"""
        labels, reasons, _ = classify_doc(mk("关于颁发田湾核电站7号机组运行许可证的通知"))
        self.assertEqual(labels, [])
        self.assertTrue(reasons[0].startswith("域门控未命中"))

    def test_out_waste_license(self):
        """放射性废物许可 → 排除"""
        labels, _, _ = classify_doc(mk("关于向中核四0四有限公司颁发放射性固体废物处置许可证的通知"))
        self.assertEqual(labels, [])

    def test_out_transport_container(self):
        """运输容器许可 → 排除（裁定 Q-L2-3）"""
        labels, _, _ = classify_doc(mk("关于不予延续中国核动力研究设计院一类放射性物品运输容器制造许可证的通知"))
        self.assertEqual(labels, [])

    def test_out_qa_program(self):
        """质量保证大纲 → 排除"""
        labels, _, _ = classify_doc(mk("关于批准《海南昌江核电厂质量保证大纲（建造阶段）》的通知"))
        self.assertEqual(labels, [])


class TestEventLabels(unittest.TestCase):
    """文档级事件（仅标题判定）"""

    def test_issue(self):
        labels, _, _ = classify_doc(mk("关于颁发中核能源科技有限公司民用核安全设备设计许可证的通知"))
        self.assertIn("事件:颁发", labels)

    def test_renew(self):
        labels, _, _ = classify_doc(mk("关于批准延续中核苏阀科技实业股份有限公司等18家单位民用核安全设备许可证的通知"))
        self.assertIn("事件:延续", labels)

    def test_renew_denied_priority(self):
        """'不予延续' 必须打上（多标签语义下可同时含'延续'，但'不予延续'不可缺失）"""
        labels, _, _ = classify_doc(mk("关于不予延续伊莱特能源装备股份有限公司民用核安全设备制造许可证的通知"))
        self.assertIn("事件:不予延续", labels)

    def test_change_scope(self):
        """'变更许可活动范围' 措辞无'证'字（M1 发现）"""
        labels, _, _ = classify_doc(mk("关于批准常州格林电力机械制造有限公司变更民用核安全设备制造许可活动范围的通知"))
        self.assertIn("事件:变更", labels)


class TestMultiLabels(unittest.TestCase):
    """裁定 Q-L2-5: 内嵌与非内嵌合并, 单文函可打多个事件标签"""

    def test_issue_plus_embedded_cancel(self):
        """颁发 + 同时注销原证（真实案例浓缩）"""
        body = ("你单位《关于××申请民用核安全设备制造许可证的请示》收悉。决定准予并颁发许可证（见附件）。"
                "同时注销无锡市华尔泰机械制造有限公司持有的民用核安全设备制造许可证〔国核安证字××号〕。"
                "国家核安全局\n2026年9月1日")
        labels, reasons, _ = classify_doc(mk("关于颁发扬州龙川能源装备有限公司民用核安全设备制造许可证的通知", body))
        self.assertIn("事件:颁发", labels)
        self.assertIn("事件:注销", labels)
        # 内嵌命中必须可追溯
        self.assertTrue(any("内嵌命中" in r for r in reasons))

    def test_register_issue_plus_embedded_change(self):
        """境外注册登记: 颁发 + 内嵌范围变更（真实案例浓缩）"""
        body = ("我局对你单位首次注册登记和法国FRAMATOME SAS注册登记范围变更的申请文件进行了审查，"
                "认为上述2家单位符合注册登记的相关条件，决定准予注册登记。")
        labels, _, _ = classify_doc(mk("关于颁发美国Mirion Technologies等2家单位民用核安全设备活动境外单位注册登记确认书的通知", body))
        self.assertIn("事件:颁发", labels)
        self.assertIn("事件:变更", labels)
        self.assertIn("证件类型:境外注册登记确认书", labels)

    def test_issue_plus_name_change(self):
        """颁发 + 内嵌单位名称变更"""
        body = "《关于民用核安全设备制造许可证单位名称变更的申请》收悉。经研究，现予颁发许可证。"
        labels, _, _ = classify_doc(mk("关于颁发成都方大炭炭复合材料股份有限公司民用核安全设备制造许可证的通知", body))
        self.assertIn("事件:颁发", labels)
        self.assertIn("事件:变更", labels)


class TestFields(unittest.TestCase):
    """证件类型 / 活动种类 / need_pdf_parse / 可追溯性"""

    def test_cert_type_license(self):
        labels, _, _ = classify_doc(mk("关于颁发××公司民用核安全设备制造许可证的通知"))
        self.assertIn("证件类型:许可证", labels)

    def test_cert_type_register(self):
        """注册登记优先于许可证（先判注册登记）"""
        labels, _, _ = classify_doc(mk("关于颁发美国××民用核安全设备活动境外单位注册登记确认书的通知"))
        self.assertIn("证件类型:境外注册登记确认书", labels)

    def test_activities_in_title(self):
        labels, _, _ = classify_doc(mk("关于批准中核苏阀科技实业股份有限公司变更民用核安全设备设计和制造许可活动范围的通知"))
        self.assertIn("活动种类:设计", labels)
        self.assertIn("活动种类:制造", labels)

    def test_need_pdf_when_attachment_exists(self):
        """有附件 → need_pdf_parse=True（变更类 70% 带附件，明细可能在附件）"""
        _, _, need_pdf = classify_doc(
            mk("关于批准延续中核苏阀科技实业股份有限公司等18家单位民用核安全设备许可证的通知",
               atts=[{"名称": "许可证", "链接": "http://x/W0.pdf"}]))
        self.assertTrue(need_pdf)

    def test_no_pdf_when_no_attachment(self):
        """无附件 → need_pdf_parse=False（明细在正文，L3 解析正文即可）"""
        _, _, need_pdf = classify_doc(mk("关于批准常州格林电力机械制造有限公司变更民用核安全设备制造许可活动范围的通知"))
        self.assertFalse(need_pdf)

    def test_input_not_mutated(self):
        """打标为纯函数: 不修改入参"""
        d = mk("关于颁发××民用核安全设备制造许可证的通知")
        before = dict(d)
        classify_doc(d)
        self.assertEqual(d, before)

    def test_reasons_traceable(self):
        """每条标签都有命中依据（match_reasons 可追溯）"""
        body = "同时注销××公司持有的民用核安全设备制造许可证。"
        _, reasons, _ = classify_doc(mk("关于颁发××民用核安全设备制造许可证的通知", body))
        self.assertTrue(any("事件[注销]" in r for r in reasons))
        self.assertTrue(any("证件类型" in r for r in reasons))


if __name__ == "__main__":
    unittest.main(verbosity=2)
