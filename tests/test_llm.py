# -*- coding: utf-8 -*-
"""tests/test_llm.py — LLM 客户端（配置占位符 / 缓存 / 容错解析）

真实 API 冒烟测试默认跳过，需显式开启：HAF_LLM_LIVE=1 python -X utf8 -m unittest tests.test_llm
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from haf import llm  # noqa: E402

LIVE = os.environ.get("HAF_LLM_LIVE") == "1"


class TestConfig(unittest.TestCase):
    def test_env_overrides(self):
        env = {"HAF_LLM_API_KEY": "sk-test", "HAF_LLM_BASE_URL": "http://x",
               "HAF_LLM_MODEL": "deepseek-flash", "HAF_LLM_TIMEOUT": "9"}
        with mock.patch.dict(os.environ, env, clear=False):
            cfg = llm.load_config()
        self.assertEqual(cfg.api_key, "sk-test")
        self.assertEqual(cfg.base_url, "http://x")
        self.assertEqual(cfg.timeout, 9)

    def test_missing_key_raises_with_hint(self):
        """key 缺失时给出明确指引（env 与 config.local.json 两处）"""
        with mock.patch.object(llm, "CONFIG_PATH", os.path.join(tempfile.gettempdir(), "no_such_config.json")):
            with mock.patch.dict(os.environ, {"HAF_LLM_API_KEY": ""}, clear=False):
                os.environ.pop("HAF_LLM_API_KEY", None)
                with self.assertRaises(llm.LLMError) as cm:
                    llm.LLMConfig()
        msg = str(cm.exception)
        self.assertIn("HAF_LLM_API_KEY", msg)
        self.assertIn("config.local.json", msg)

    def test_direct_config_reads_local_file(self):
        """LLMConfig() 直接实例化时也能从 config.local.json 取 key"""
        path = os.path.join(tempfile.mkdtemp(), "config.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"api_key": "sk-direct-read"}, f)
        with mock.patch.object(llm, "CONFIG_PATH", path), \
                mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HAF_LLM_API_KEY", None)
            self.assertEqual(llm.LLMConfig().api_key, "sk-direct-read")

    def test_template_placeholder_is_not_accepted(self):
        """config.example.json 的占位符不应被当作有效 key"""
        path = os.path.join(tempfile.mkdtemp(), "config.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"api_key": "sk-在此填入你的密钥"}, f)
        with mock.patch.object(llm, "CONFIG_PATH", path):
            os.environ.pop("HAF_LLM_API_KEY", None)
            with self.assertRaises(llm.LLMError):
                llm.LLMConfig()

    def test_load_config_from_file(self):
        """config.local.json 的字段可被读取（模板不参与，只在临时路径验证）"""
        path = os.path.join(tempfile.mkdtemp(), "config.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"api_key": "sk-from-file", "model": "deepseek-flash",
                       "base_url": "http://file-host", "timeout": "30"}, f)
        cfg = llm.load_config(config_path=path)
        self.assertEqual(cfg.api_key, "sk-from-file")
        self.assertEqual(cfg.base_url, "http://file-host")
        self.assertEqual(cfg.timeout, 30)

    def test_env_overrides_file(self):
        path = os.path.join(tempfile.mkdtemp(), "config.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"api_key": "sk-from-file"}, f)
        with mock.patch.dict(os.environ, {"HAF_LLM_API_KEY": "sk-from-env"}, clear=False):
            cfg = llm.load_config(config_path=path)
        self.assertEqual(cfg.api_key, "sk-from-env")

    def test_no_key_file_fallback(self):
        """已废弃的方法不应再被支持（无 HAF_LLM_KEY_FILE 分支）"""
        self.assertFalse(hasattr(llm, "DEFAULT_KEY_FILE"))
        self.assertTrue(hasattr(llm, "CONFIG_PATH"))

    def test_describe_masks_key(self):
        cfg = llm.LLMConfig(api_key="sk-1234567890abcdef", base_url="http://x", model="deepseek-flash")
        d = cfg.describe()
        self.assertNotIn("1234567890", d)
        self.assertIn("sk-1", d)

    def test_default_model_is_flash_and_thinking_disabled(self):
        cfg = llm.LLMConfig(api_key="sk-test")
        self.assertEqual(cfg.model, "deepseek-flash")
        self.assertEqual(cfg.thinking, "disabled")


class TestJsonParsing(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(llm.LLMClient._parse_json('{"a":1}'), {"a": 1})

    def test_fenced_json(self):
        self.assertEqual(llm.LLMClient._parse_json('```json\n{"a":1}\n```'), {"a": 1})

    def test_json_with_prose(self):
        self.assertEqual(llm.LLMClient._parse_json('结果如下：{"a":1} 完毕'), {"a": 1})

    def test_broken_json_raises(self):
        with self.assertRaises(Exception):
            llm.LLMClient._parse_json("不是 JSON")


class TestCacheAndUsage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig_cache = llm.CACHE_DIR
        llm.CACHE_DIR = self.tmp
        self.client = llm.LLMClient(llm.LLMConfig(api_key="sk-test"), cache=True, verbose=False)

    def tearDown(self):
        llm.CACHE_DIR = self._orig_cache

    def _post(self, payload):
        return {"choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}

    def test_single_call_then_cache_hit(self):
        with mock.patch.object(self.client, "_post", side_effect=self._post) as m:
            r1, meta1 = self.client.chat_json("sys", "user", tag="t1")
            r2, meta2 = self.client.chat_json("sys", "user", tag="t1")
        self.assertEqual(r1, {"ok": True})
        self.assertEqual(r2, {"ok": True})
        self.assertEqual(m.call_count, 1)                    # 第二次命中缓存，不再请求
        self.assertFalse(meta1["cached"])
        self.assertTrue(meta2["cached"])
        self.assertEqual(self.client.usage["calls"], 1)
        self.assertEqual(self.client.usage["cached"], 1)
        self.assertEqual(self.client.usage["prompt_tokens"], 10)

    def test_thinking_flag_passed(self):
        with mock.patch.object(self.client, "_post", side_effect=self._post) as m:
            self.client.chat_json("s", "u")
        payload = m.call_args[0][0]
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["model"], "deepseek-flash")

    def test_retry_then_success(self):
        calls = {"n": 0}

        def flaky(payload):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("网络抖动")
            return self._post(payload)

        with mock.patch.object(self.client, "_post", side_effect=flaky), \
                mock.patch("time.sleep", lambda *_: None):
            r, _ = self.client.chat_json("s", "u")
        self.assertEqual(r, {"ok": True})
        self.assertEqual(calls["n"], 2)

    def test_usage_file_written(self):
        with mock.patch.object(self.client, "_post", side_effect=self._post):
            self.client.chat_json("s", "u")
        usage_file = os.path.join(self.tmp, "_usage.json")
        self.assertTrue(os.path.exists(usage_file))
        data = json.load(open(usage_file, encoding="utf-8"))
        self.assertTrue(data["sessions"])


@unittest.skipUnless(LIVE, "设置 HAF_LLM_LIVE=1 才跑真实 API 冒烟测试")
class TestLiveApi(unittest.TestCase):
    def test_live_json_call(self):
        client = llm.LLMClient(cache=False, verbose=False)
        data, meta = client.chat_json(
            "你是字段抽取器，只输出 JSON。",
            '输出 {"证字号": "国核安证字S（26）16号"}',
            tag="live-smoke")
        self.assertIn("证字号", data)
        self.assertGreater(meta["completion_tokens"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
