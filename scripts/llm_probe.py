# -*- coding: utf-8 -*-
"""LLM 探测三：deepseek-flash 的思考开关与输出开销（不打印 key）

配置来源与流水线一致：HAF_LLM_* 环境变量 → config.local.json
"""
import json
import os
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from haf.llm import load_config  # noqa: E402

_cfg = load_config()
BASE = _cfg.base_url
KEY = _cfg.api_key

SYS = "你是字段抽取器，只输出 JSON。"
USER = "输出 {\"ok\": true}"


def call(extra, model="deepseek-flash"):
    payload = {"model": model, "messages": [{"role": "system", "content": SYS},
                                            {"role": "user", "content": USER}],
               "response_format": {"type": "json_object"}, "max_tokens": 2000}
    payload.update(extra)
    req = urllib.request.Request(f"{BASE}/chat/completions", data=json.dumps(payload).encode(),
                                 headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            b = json.loads(r.read().decode())
        u = b.get("usage", {})
        rc = b["choices"][0]["message"].get("reasoning_content") or ""
        return (f"OK {time.time()-t0:5.1f}s out={u.get('completion_tokens')} "
                f"reasoning={len(rc)} content={(b['choices'][0]['message'].get('content') or '')[:40]!r}")
    except Exception as e:  # noqa: BLE001
        return f"FAIL {type(e).__name__}: {str(e)[:150]}"


for name, extra in [
    ("基线", {}),
    ("thinking=disabled", {"thinking": {"type": "disabled"}}),
    ("reasoning_effort=low", {"reasoning_effort": "low"}),
    ("enable_thinking=false", {"enable_thinking": False}),
    ("chat_template_kwargs", {"chat_template_kwargs": {"enable_thinking": False}}),
]:
    print(f"  {name:24s} -> {call(extra)}")
