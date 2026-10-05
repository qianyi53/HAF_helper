# -*- coding: utf-8 -*-
"""haf/state.py — 增量解析状态（按附件 URL + 内容指纹判新旧，实现"只解析新增/变更"）

状态文件 data/parse_state.json 结构:
{
  "meta": {"last_run": "...", "last_summary": {...}},
  "attachments": {
     "<附件URL>": {"doc_id": "...", "sha1": "abc123", "parsed_at": "...",
                   "records": 26, "mode": "rule"}
  }
}
"""
import json
import os
import tempfile
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(ROOT, "data", "parse_state.json")


def load(path=STATE_PATH):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"meta": {}, "attachments": {}}


def save(path, state):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def key(url, doc_id):
    """状态键 = 文函 + 附件（同一附件 URL 可能被多篇文函引用，需各自记录）"""
    return f"{doc_id}|{url}"


def is_fresh(state, url, sha1, doc_id=None, llm=False):
    """同一（文函, 附件）、内容指纹未变、且解析模式一致 → 视为已解析，可跳过

    llm 记录本轮是否启用 LLM 兜底：关闭 LLM 跑出的结果不应用于启用 LLM 的轮次，
    反之亦然（避免切换模式后误复用质量较低的结果）。
    """
    rec = (state.get("attachments") or {}).get(key(url, doc_id))
    if not (rec and sha1 and rec.get("sha1") == sha1):
        return False
    return bool(rec.get("llm")) == bool(llm)


def mark(state, url, doc_id, sha1, records, mode, llm=False, when=None):
    state.setdefault("attachments", {})[key(url, doc_id)] = {
        "doc_id": doc_id,
        "url": url,
        "sha1": sha1,
        "parsed_at": when or datetime.now().isoformat(timespec="seconds"),
        "records": records,
        "mode": mode,
        "llm": bool(llm),
    }


def prune(state, valid_keys):
    """移除已不在本轮范围内的状态（官网撤下 / 文函关系变化）"""
    atts = state.get("attachments") or {}
    removed = [k for k in atts if k not in valid_keys]
    for k in removed:
        atts.pop(k, None)
    return removed


def finish(state, summary):
    state.setdefault("meta", {})["last_run"] = datetime.now().isoformat(timespec="seconds")
    state["meta"]["last_summary"] = summary
    return state
