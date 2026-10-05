# haf-sync — SQLite → 云数据库增量同步

把本地 `haf.db`（SQLite）按主键做增量同步到 WorkBuddy 云数据库（应用 `HAF许可证库`）。

## 前置要求

- Node.js ≥ 22.5（用到内置 `node:sqlite`，无需任何原生依赖）
- 首次在新机器运行前，在本目录执行一次 `npm install`

## 配置

编辑 `config.json`：

| 字段 | 说明 |
|---|---|
| `db` | 默认的 SQLite 文件路径（命令行传入路径可覆盖） |
| `syncSecret` | 云端写通道密钥（与数据库 `app_secrets` 表中的值一致；**不要提交到公共仓库**） |
| `endpoint` / `publishableKey` | 应用云服务公共配置，勿改 |

## 用法

```bash
# 常规增量同步（用 config.json 里的 db 路径）
node sync.mjs

# 指定一个新版本的 haf.db
node sync.mjs "E:\workspace\HAF_info\data\db\haf.db"

# 只看差异计划，不写云端
node sync.mjs --dry-run

# 强制把本地全部行重新 upsert 一遍（对账/修复用）
node sync.mjs --full

# 只同步某几张表
node sync.mjs --table=licenses --table=changes
```

## 同步语义

- 以每张表的 TEXT 主键为锚点做三方 diff：
  - **本地有、云端无或内容有变化** → 整行 upsert（`haf_sync_apply`，DELETE+INSERT 整行替换）
  - **云端有、本地无** → 删除（`haf_sync_delete`）
  - **内容一致** → 跳过（第二遍运行应报告全 0，可用于验证）
- 运行结束生成 `last-sync-report.json`，含每张表的 local/cloud/upsert/delete 统计。
- 同步是幂等的：同数据重跑不会产生任何写入。

## 安全模型

- 云端 6 张数据表对公网**只读**（RLS 只放行 SELECT）。
- 写入只能通过 `haf_sync_apply` / `haf_sync_delete` 两个 SECURITY DEFINER 函数，
  调用必须携带 `syncSecret`；网页前端拿不到该密钥，无法篡改数据。
- 密钥只存在本机 `config.json` 里。泄露后请让我在云端换掉 `app_secrets` 里的值，
  再同步更新本地 `config.json` 即可。

## 表清单

| 表 | 主键 |
|---|---|
| documents | doc_id |
| licenses | license_id |
| license_versions | version_id |
| changes | change_id |
| registry_scopes | registry_scope_id |
| scope_items | scope_item_id |

## 微信版（workbuddy.link 轻量发布）

浏览器版走云数据库实时查询；**微信版**是另一个入口——数据打包进单页 HTML（约 1.5MB，
零外部依赖），发布到资料库 `workbuddy.link/p/7OEQPlBlwMInhX1YnpvE0Z`，微信可直接打开。

- 构建：`frontend/build_wechat.py`（Python，读 haf.db 导出三数据集注入 `frontend/wechat_template.html`，
  产出 `frontend/wechat/核安全设备许可证查询.html`）
- 发布：导入+发布依赖 WorkBuddy 会话（资料库 import/publish 脚本），**无法纯脚本完成**——
  数据更新时说一句「同步微信版」即可，流程 = build_wechat.py 重跑 → 重导入节点
  `7OEQPlBlwMInhX1YnpvE0Z` → 重新发布，链接不变
- 两条线的数据口径一致（同一份 haf.db，当前版本链尾快照）
