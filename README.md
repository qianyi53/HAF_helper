# HAF_info

国家核安全局（NNSA）核安全设备监管文函聚合与查询系统：**采集 → 筛选打标 → PDF 解析 → SQLite 数据库交付 → 单页 HTML 前端查询**（L1~L5）。

## 快速开始

```powershell
# 0. 虚拟环境（已建好 .venv）
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 1. 采集（L1，增量；沿用已存 cutoff）
python -X utf8 scripts/nnsa_crawler.py

# 2. 筛选打标（L2）→ data/labeled_docs.json
python -X utf8 -m haf.classifier

# 3. PDF 解析（L3）→ data/parsed_records.json
python -X utf8 -m haf.pipeline --stage l3                 # 推荐：规则优先 + LLM 兜底
python -X utf8 -m haf.pipeline --stage l3 --no-llm        # 纯规则（不调 LLM）
python -X utf8 -m haf.pipeline --stage l3 --limit 20      # 试跑 20 个附件

# 4. 建库（L4）→ data/db/haf.db（交付物）
python -X utf8 -m haf.pipeline --stage l4
python -X utf8 -m haf.pipeline --stage l4 --csv           # 额外导出 CSV 分表

# 5. 前端（L5）
python -X utf8 -m haf.pipeline                            # 周更一键 = L1→L2→L3增量→L4
cd frontend\haf-sync && node sync.mjs && cd ..\..         # 同步云数据库（浏览器版数据源）
.\.venv\Scripts\python.exe -X utf8 frontend\build_wechat.py   # 重建微信版单页
```

## 交付物

### 数据库（L4）

`data/db/haf.db` —— SQLite 单文件（6 表 + 2 视图 + 物理外键）：

| 对象 | 说明 |
|---|---|
| `documents` | 文函主表（L1/L2 透传） |
| `licenses` | 许可证（一证一条，含状态：有效/无效/已注销） |
| `license_versions` | 版本链（每次颁发/延续/变更一版；证字号是版本属性） |
| `scope_items` | **许可活动范围明细（设备粒度）**，能力特征参数为 JSON |
| `changes` | 变更记录（含 diff 明细：新增/删除/修改） |
| `registry_scopes` | 境外单位注册登记范围（表一设计/表二制造） |
| `view_license_current` | 可读主表：单位、证字号、状态、有效期、变更次数、官网链接、附件页码 |
| `view_device_index` | 设备检索视图：按设备类别/品种查最新能力特征参数 + 证字号 + 原文链接 |

### 前端（L5，单页 HTML）

| 入口 | 产出 | 说明 |
|---|---|---|
| 浏览器版 | `frontend/index.html` | 双入口（按设备/按单位）+ 排序/分页/详情抽屉/变更 diff；实时只读查询云数据库。已发布：https://haf-license.app.workbuddy.host/ |
| 微信版 | `frontend/wechat/核安全设备许可证查询.html` | 数据内嵌单页（~1.5MB，零外部依赖），由 `build_wechat.py` 从 haf.db 构建。已发布：https://workbuddy.link/p/7OEQPlBlwMInhX1YnpvE0Z |
| 云同步 | `frontend/haf-sync/sync.mjs` | haf.db → 云数据库按主键增量同步（幂等；浏览器版数据随周更自动更新） |

## 目录结构

| 路径 | 说明 |
|---|---|
| `docs/` | `01-需求说明`、`02-架构设计`、`03-开发计划`、`04-使用说明`、`05-业务逻辑说明`、`06-数据字典`（交付库字段与 JSON 字段解析说明）、`问答对齐`（裁定留档）、`reports/`（分析/开发/测试报告） |
| `scripts/` | 独立脚本：`nnsa_crawler.py`（L1）、M3 分析脚本、夹具生成、验收查询 |
| `haf/` | 核心包：`rules.py`/`classifier.py`（L2）、`pdf_fetch.py`/`pdf_parser.py`/`llm.py`/`chain.py`/`state.py`（L3）、`db_builder.py`/`pipeline.py`（L4） |
| `frontend/` | **L5 前端**：`index.html`（浏览器版）、`wechat_template.html` + `build_wechat.py` + `wechat/`（微信版）、`haf-sync/`（云数据库同步，含独立 README）、`wrapper/`（包装页留档） |
| `tests/` | 单元测试（93 项）+ `fixtures/` 真实公文裁剪样本 |
| `data/` | 各阶段产物；`data/db/haf.db` 为交付物 |
| `archive/` | 历史验证产物；`HAF-agent-git-backup/` 为并入前旧仓库备份（不入库） |
| `.workbuddy/` | AI 开发记忆（勿删） |

## LLM 配置（占位符）

按顺序读取：环境变量 `HAF_LLM_API_KEY` / `HAF_LLM_BASE_URL` / `HAF_LLM_MODEL` → 项目根 `config.local.json`（不入 git，模板见 `config.example.json`）。

默认调用 `deepseek-flash`，并关闭思考链（实测更快更省）；结果按块缓存于 `data/llm_cache/`（重跑不重复计费）。

## 测试

```powershell
python -X utf8 -m unittest discover -s tests -t .
$env:HAF_LLM_LIVE="1"; python -X utf8 -m unittest tests.test_llm   # 含真实 API 冒烟
```

详见 `docs/02-架构设计.md`、`docs/03-开发计划.md`、`docs/04-使用说明.md`。
