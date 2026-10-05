# -*- coding: utf-8 -*-
"""tests/test_state.py — 增量状态（内容指纹判新旧 / 剪枝 / 汇总）"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from haf import state  # noqa: E402


class TestState(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "state.json")
        self.st = {"meta": {}, "attachments": {}}

    def test_fresh_by_sha1(self):
        state.mark(self.st, "http://a/1.pdf", "d1", "sha1aaa", 3, "rule")
        self.assertTrue(state.is_fresh(self.st, "http://a/1.pdf", "sha1aaa", "d1"))
        self.assertFalse(state.is_fresh(self.st, "http://a/1.pdf", "sha1bbb", "d1"))   # 内容变更
        self.assertFalse(state.is_fresh(self.st, "http://a/2.pdf", "sha1aaa", "d1"))   # 新附件
        self.assertFalse(state.is_fresh(self.st, "http://a/1.pdf", None, "d1"))        # 无指纹

    def test_same_attachment_different_docs_are_independent(self):
        """同一附件被多篇文函引用时，各文函各自记录（键 = 文函|附件）"""
        state.mark(self.st, "http://a/shared.pdf", "d1", "s1", 2, "rule")
        self.assertTrue(state.is_fresh(self.st, "http://a/shared.pdf", "s1", "d1"))
        self.assertFalse(state.is_fresh(self.st, "http://a/shared.pdf", "s1", "d2"))
        self.assertEqual(len(self.st["attachments"]), 1)

    def test_llm_mode_switch_invalidates_state(self):
        """关闭 LLM 的旧结果不应用于启用 LLM 的轮次（反之亦然）"""
        state.mark(self.st, "http://a/1.pdf", "d1", "s1", 3, "rule", llm=False)
        self.assertTrue(state.is_fresh(self.st, "http://a/1.pdf", "s1", "d1", llm=False))
        self.assertFalse(state.is_fresh(self.st, "http://a/1.pdf", "s1", "d1", llm=True))
        state.mark(self.st, "http://a/1.pdf", "d1", "s1", 3, "llm(补全)", llm=True)
        self.assertTrue(state.is_fresh(self.st, "http://a/1.pdf", "s1", "d1", llm=True))

    def test_save_load_roundtrip(self):
        state.mark(self.st, "http://a/1.pdf", "d1", "sha1aaa", 3, "rule")
        state.finish(self.st, {"parsed": 1})
        state.save(self.path, self.st)
        loaded = state.load(self.path)
        self.assertEqual(loaded["attachments"][state.key("http://a/1.pdf", "d1")]["sha1"], "sha1aaa")
        self.assertIn("last_run", loaded["meta"])
        self.assertEqual(loaded["meta"]["last_summary"]["parsed"], 1)

    def test_load_missing_returns_empty(self):
        self.assertEqual(state.load(os.path.join(tempfile.gettempdir(), "no_such_state.json")),
                         {"meta": {}, "attachments": {}})

    def test_prune(self):
        state.mark(self.st, "http://a/keep.pdf", "d1", "s1", 1, "rule")
        state.mark(self.st, "http://a/drop.pdf", "d2", "s2", 1, "rule")
        removed = state.prune(self.st, {state.key("http://a/keep.pdf", "d1")})
        self.assertEqual(removed, [state.key("http://a/drop.pdf", "d2")])
        self.assertEqual(len(self.st["attachments"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
