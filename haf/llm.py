# -*- coding: utf-8 -*-
"""haf/llm.py — L3 轻量 LLM 客户端（占位符配置 + 块级缓存）

配置读取顺序（裁定 Q-L3-1）:
  1. 环境变量: HAF_LLM_API_KEY / HAF_LLM_BASE_URL / HAF_LLM_MODEL / HAF_LLM_TIMEOUT / HAF_LLM_MAX_TOKENS
  2. 项目根 config.local.json（不入 git，模板见 config.example.json）:
     {provider, base_url, api_key, model, thinking, temperature, max_tokens, timeout, max_retries}

调用约定: 统一走 chat_json()（response_format=json_object），结果按「模型+提示词」哈希缓存到
data/llm_cache/，重跑不重复计费；用量累计写入 data/llm_cache/_usage.json。
"""
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config.local.json")
CACHE_DIR = os.path.join(ROOT, "data", "llm_cache")

DEFAULTS = {
    "provider": "deepseek",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-flash",      # 裁定 Q-L3-1：调用 flash
    "temperature": 0,
    "max_tokens": 8000,          # 大块（十余行范围明细）输出较长，留足预算
    "timeout": 180,
    "max_retries": 3,
    # flash 为推理模型：字段抽取不需要思考链，关闭后输出 token 与耗时显著下降
    # （实测 19→5 tok、0.9s→0.6s）；如需开启可设 "enabled"
    "thinking": "disabled",
}


class LLMError(RuntimeError):
    """LLM 调用失败（配置缺失/网络/接口错误）"""


class LLMConfig:
    def __init__(self, **kw):
        self.__dict__.update(DEFAULTS)
        self.__dict__.update({k: v for k, v in kw.items() if v is not None})
        self.api_key = kw.get("api_key") or self._load_key()

    def _load_key(self):
        """key 来源：环境变量 → config.local.json；都没有则明确报错（不静默降级）"""
        key = os.environ.get("HAF_LLM_API_KEY")
        if key:
            return key.strip()
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, encoding="utf-8") as f:
                    cfg_key = (json.load(f) or {}).get("api_key")
                if cfg_key and not str(cfg_key).startswith("sk-在此"):
                    return str(cfg_key).strip()
            except Exception:  # noqa: BLE001 配置损坏时按缺失处理
                pass
        raise LLMError(
            "未找到 API key：请设置环境变量 HAF_LLM_API_KEY，"
            "或在项目根 config.local.json 中填写 api_key（模板见 config.example.json）；"
            "如需纯规则运行请加 --no-llm")

    def describe(self):
        k = self.api_key or ""
        return f"{self.provider}/{self.model} @ {self.base_url} (key {k[:4]}****{k[-2:] if len(k) > 6 else ''})"


def load_config(config_path=None, **override):
    """读取配置：env > config.local.json；缺失 api_key 时抛 LLMError"""
    cfg = {}
    path = config_path or CONFIG_PATH
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    env_map = {"HAF_LLM_BASE_URL": "base_url", "HAF_LLM_MODEL": "model",
               "HAF_LLM_TIMEOUT": "timeout", "HAF_LLM_API_KEY": "api_key",
               "HAF_LLM_MAX_TOKENS": "max_tokens"}
    for env, field in env_map.items():
        if os.environ.get(env):
            cfg[field] = os.environ[env]
    for k in ("timeout", "max_tokens", "max_retries"):
        if cfg.get(k) is not None:
            cfg[k] = int(cfg[k])
    cfg.update({k: v for k, v in override.items() if v is not None})
    return LLMConfig(**cfg)


class LLMClient:
    """块级 LLM 调用器：缓存 + 重试 + 用量统计"""

    def __init__(self, config=None, cache=True, verbose=True):
        self.cfg = config or load_config()
        self.cache = cache
        self.verbose = verbose
        self.usage = {"calls": 0, "cached": 0, "prompt_tokens": 0,
                      "completion_tokens": 0, "total_tokens": 0, "seconds": 0.0}
        if cache:
            os.makedirs(CACHE_DIR, exist_ok=True)

    # ---------- 缓存 ----------
    def _cache_path(self, system, user):
        h = hashlib.md5(f"{self.cfg.model}\x00{system}\x00{user}".encode()).hexdigest()
        return os.path.join(CACHE_DIR, f"{h}.json")

    # ---------- 主调用 ----------
    def chat_json(self, system, user, tag=""):
        """返回 (dict_or_list, meta)；meta: {cached, prompt_tokens, completion_tokens, seconds}"""
        cp = self._cache_path(system, user)
        if self.cache and os.path.exists(cp):
            with open(cp, encoding="utf-8") as f:
                obj = json.load(f)
            self.usage["cached"] += 1
            if self.verbose:
                print(f"    [llm] 命中缓存 {tag}")
            return obj["result"], {"cached": True, "prompt_tokens": 0,
                                   "completion_tokens": 0, "seconds": 0.0}

        payload = {
            "model": self.cfg.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_tokens,
            "response_format": {"type": "json_object"},
        }
        thinking = getattr(self.cfg, "thinking", "") or ""
        if thinking in ("disabled", "enabled"):
            payload["thinking"] = {"type": thinking}
        last = None
        for attempt in range(1, self.cfg.max_retries + 1):
            t0 = time.time()
            try:
                body = self._post(payload)
                dt = time.time() - t0
                content = (body["choices"][0]["message"].get("content") or "").strip()
                result = self._parse_json(content)
                u = body.get("usage") or {}
                meta = {"cached": False, "prompt_tokens": u.get("prompt_tokens", 0),
                        "completion_tokens": u.get("completion_tokens", 0), "seconds": dt}
                self.usage["calls"] += 1
                self.usage["prompt_tokens"] += meta["prompt_tokens"]
                self.usage["completion_tokens"] += meta["completion_tokens"]
                self.usage["total_tokens"] += u.get("total_tokens", 0)
                self.usage["seconds"] += dt
                if self.cache:
                    with open(cp, "w", encoding="utf-8") as f:
                        json.dump({"tag": tag, "result": result, "usage": u,
                                   "model": self.cfg.model}, f, ensure_ascii=False)
                if self.verbose:
                    print(f"    [llm] {tag} {dt:.1f}s in={meta['prompt_tokens']} "
                          f"out={meta['completion_tokens']}")
                self.save_usage()
                return result, meta
            except Exception as e:  # noqa: BLE001
                last = e
                if attempt < self.cfg.max_retries:
                    wait = 2 * attempt
                    if self.verbose:
                        print(f"    [llm] 第{attempt}次失败({e})，{wait}s 后重试")
                    time.sleep(wait)
        raise LLMError(f"LLM 调用失败（{self.cfg.model}）: {last}")

    def _post(self, payload):
        req = urllib.request.Request(
            f"{self.cfg.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {self.cfg.api_key}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
            return json.loads(resp.read().decode())

    @staticmethod
    def _parse_json(text):
        """容错解析：去 markdown 围栏 / 截取 JSON 片段 / 修复被 max_tokens 截断的 JSON"""
        text = (text or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        m = re.search(r"[\{\[].*[\}\]]", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        fixed = LLMClient._repair_truncated(text)
        if fixed is not None:
            return fixed
        raise json.JSONDecodeError("无法解析模型输出", text[:200], 0)

    @staticmethod
    def _repair_truncated(text):
        """截断修复：逐字符扫描（跳过字符串内部），补齐未闭合的 []{} 与引号"""
        start = text.find("{")
        if start < 0:
            return None
        buf, stack, in_str, esc = [], [], False, False
        for ch in text[start:]:
            buf.append(ch)
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch in "[{":
                stack.append("]" if ch == "[" else "}")
            elif ch in "]}":
                if stack and stack[-1] == ch:
                    stack.pop()
        if in_str:
            buf.append('"')
        # 去掉末尾可能不完整的键值（如 "备注": 或 "备注":"未写完）
        tail = "".join(buf)
        tail = re.sub(r',\s*"[^"]*"\s*:\s*$', "", tail)
        tail = re.sub(r',\s*$', "", tail)
        tail += "".join(reversed(stack))
        try:
            return json.loads(tail)
        except json.JSONDecodeError:
            return None

    # ---------- 用量落盘 ----------
    def save_usage(self):
        if not self.cache:
            return
        path = os.path.join(CACHE_DIR, "_usage.json")
        hist = {"sessions": []}
        if os.path.exists(path):
            try:
                hist = json.load(open(path, encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass
        hist["sessions"] = [s for s in hist.get("sessions", []) if s.get("session_id") != self._sid()] + [
            {"session_id": self._sid(), "model": self.cfg.model, **self.usage}]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False, indent=1)

    def _sid(self):
        if not hasattr(self, "_session_id"):
            self._session_id = time.strftime("%Y%m%dT%H%M%S")
        return self._session_id

    def summary(self):
        u = self.usage
        return (f"LLM 调用 {u['calls']} 次（缓存命中 {u['cached']}）"
                f" | 输入 {u['prompt_tokens']} tok / 输出 {u['completion_tokens']} tok"
                f" | 耗时 {u['seconds']:.1f}s")
