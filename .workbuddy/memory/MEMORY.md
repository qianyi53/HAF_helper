# 项目长期备忘 — HAF 许可证库（HAF_info，L1~L5）

## 项目（2026-10-05 合并定稿）
- **唯一项目目录 = 工作区 `E:\workspace\HAF_info`**，不再嵌套 HAF-agent；分层：L1 采集（scripts/nnsa_crawler.py）→ L2 筛选（haf/rules+classifier）→ L3 解析（haf/pdf_*+llm+chain+state）→ L4 建库（haf/db_builder+pipeline）→ **L5 前端（frontend/：index.html 浏览器版、build_wechat.py+wechat_template+wechat/ 微信版、haf-sync/ 云同步、wrapper/ 留档）**
- 前端原为独立工作区（HAF前端），2026-10-05 并入；合并报告 docs/reports/2026-10-05-【开发】-L5前端融合与项目合并开发报告.md
- 旧 HAF-agent git 历史备份：archive/HAF-agent-git-backup/（gitignore，仅本机）

## 应用
- 应用名：HAF许可证库；applicationId `wbapp_YGFKLa2QDfDKLyAjkfLitn`
- endpoint：`https://haf-license.app.workbuddy.host`（publicConfig 另含 publishableKey，存于 frontend/haf-sync/config.json 与云端激活记录）
- 数据源：`E:\workspace\HAF_info\data\db\haf.db`（民用核安全设备许可证/文函/变更/范围明细）

## 云数据库结构（public schema）
- 表：documents, licenses, license_versions, changes, registry_scopes, scope_items（均 TEXT 主键、中文列名、无外键、JSON 存 TEXT）
- 视图：view_license_current, view_device_index
- RLS：6 张表 anon/authenticated 只读；app_secrets 不可达
- 写通道：haf_sync_apply / haf_sync_delete（SECURITY DEFINER + app_secrets 中的 haf_sync 密钥）

## 维护约定
- 数据更新跑 `frontend/haf-sync/sync.mjs`（脚本化，无需新会话）；--dry-run 预览、--full 强制对账
- 前端 = 轻量化单页 HTML（浏览器版读云数据库；微信版数据内嵌；表只读，页面无写功能）
- SDK：@tencent-ai/workbuddy-cloud-sdk@dev；Node 无头使用时 fetch 注入 Origin=endpoint（已验证可行）

## 两个发布入口（数据同源 haf.db，口径=当前版本链尾）
- 浏览器版：https://haf-license.app.workbuddy.host/ （frontend/index.html，实时查云数据库，haf-sync 自更新）
- 微信版：https://workbuddy.link/p/7OEQPlBlwMInhX1YnpvE0Z （资料库页面，数据内嵌单页 ~1.5MB，零外链）
  - 编辑态：https://www.workbuddy.cn/space/d/7OEQPlBlwMInhX1YnpvE0Z
  - 构建：`frontend/build_wechat.py`（导出 device 2565/lic 552/chg 88 注入 `frontend/wechat_template.html`）
  - 更新流程「同步微信版」：重跑 build → import_html.py --node-block-id 7OEQPlBlwMInhX1YnpvE0Z → publish_page.py（需 WorkBuddy 会话）
  - 设备索引许可证级字段（单位/证字号/状态/链接）导出时去重，前端运行时从 lic 合并（数据生效日期=v.生效日期 存于 lic）
  - 资料库导入硬门槛：页面不得有外部 img/script/link 资源（外链 <a> 导航不受限）；favicon 用 data: URI
