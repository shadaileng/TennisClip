# 服务商管理对齐 TennisDiary

> **本页信息**
>
> | 项目 | 内容 |
> |------|------|
> | 文档编号 | 06 |
> | 文档版本 | v1.0.0 |
> | 文档状态 | 🏁 已完成 |
> | 最后更新 | 2026-09-17 |
> | 对应功能/内容 | 模型服务商管理完全对齐 TennisDiary：配置 KV 驱动激活/选模型、ai_providers 表重构、校验模型探测、id 路由、前端管理弹窗 |
>
> **变更历史**
>
> | 日期 | 版本 | 说明 |
> |------|:----:|------|
> | 2026-09-17 | v1.0.0 | 初版 |
>
> **关联文档**：[05：模型提供商多模型管理](./05-模型提供商多模型管理.md)、[04：前端模型选择功能](./04-前端模型选择功能.md)、[根目录 AGENTS.md](../AGENTS.md)、[参考实现：TennisDiary](../reference/TennisDiary)（docs/reference 不入库）

## 一、背景与目标

当前 TennisClip 的模型服务商管理（`model_providers` 表）把「谁是激活服务商（`is_active`）」「用哪个模型（`selected_model`）」直接存进 provider 行内，前端用 `activate` / `select-model` 两个端点切换。这与 [TennisDiary](../reference/TennisDiary) 的「配置 KV 驱动」模式不一致，难以复用其经过验证的管理弹窗与处理链路。

**目标**：完全对齐 TennisDiary 的「管理服务商弹窗」与后台实现——

- 后端：引入 `system_config` 配置 KV 表 + `config_service`，将「激活服务商（`ai.provider`）」「选定模型（`ai.model`）」抽离为配置覆盖；`model_providers` 重构为 `ai_providers`（纯凭据目录：`id` PK、`enabled` 用 int、去除 `is_active`/`selected_model`）。
- 新增「校验模型」探测接口（`check-models`，list + probe）。
- 编辑/删除路由改按整数 `id`。
- 前端：接入与 TennisDiary 一致的服务商管理弹窗（表格 + 内联多模型表单 + 逐模型 ✓/✗ 校验），上传面板 provider/model 下拉改为配置直选。
- **后端采用 TDD 模式**：先写失败测试（Red），再实现（Green），最后重构（Refactor）。

## 二、方案设计

### 2.1 配置 KV 子系统（核心策略）

把「激活服务商 / 选定模型」从 provider 行内列抽离到独立 `system_config` KV，provider 表退化为纯凭据目录（与 TennisDiary `ai_providers` 完全一致）。

- 新增 `backend/app/core/config_registry.py`：最小注册表，含 `ai.provider`（select）、`ai.model`（str）、`ai.api_key`（secret）、`ai.base_url`（url）。默认值取自 `AppConfig`（`ai.provider` 默认 = `config.llm.active_provider`，首启动即有选中项）。
- 新增 `backend/app/services/config_service.py`：
  - `mask_secret(value)` → 空返回 `''`；`len<=8` 返回 `'****'`；否则 `f"{value[:3]}****{value[-4:]}"`（统一为 TennisDiary 语义，替换原首尾各 4 位）。
  - `get_config_value(db, key)` / `set_config_value(db, key, value)` / `delete_config_value(db, key)`：DB 覆盖 > 默认值。
  - `get_ai_config(db, config)`：读 `ai.provider`，命中启用服务商则引用其 `api_key`/`base_url`，`model = ai.model 覆盖或 default_model`；否则回落静态 `AppConfig`。
  - `list_provider_options(db)`：启用服务商 name + `custom`。

### 2.2 ORM 重构（db_models.py）

`model_providers` → `ai_providers`，对齐 TennisDiary `ai_provider.py`：

- 列：`id` Integer PK autoincrement、`name` String(64) unique index、`base_url` String(255) not null、`api_key` Text nullable、`models` JSONList（Text 存 JSON 数组）、`enabled` Integer default 1、`sort_order` Integer default 0、`created_at` Float default 0、`updated_at` DateTime server_default now onupdate。
- 删除 `is_active`、`selected_model`；`enabled` 由 Boolean 改 Integer（与 TennisDiary 一致）。
- 新增 `default_model` 属性 = `models[0]`；保留 `JSONList` TypeDecorator（已实现，一致）。
- 新增 `SystemConfig` KV 模型：`key` String unique、`value` Text。
- `db.py` 确保两个模型被 import 纳入 metadata；`_seed` 改为写 `ai_providers`，且 `ai.provider` 默认取 `active_provider`。

### 2.3 CRUD / 路由（db_service.py + main.py）

- 改按整数 `id`：GET/POST `/api/v1/db/providers`，PUT/DELETE `/api/v1/db/providers/{id}`。
- `_build_provider_item(p, selected_name)` → 含 `id`、`name`、`base_url`、`api_key`（掩码）、`models`、`default_model`、`enabled`(bool)、`sort_order`、`is_selected=(p.name==selected_name)`、`updated_at`。
- `create_provider` / `update_provider(id)`：name 唯一校验（重复 400，TennisDiary 用 400，注：AGENTS.md 旧契约写 409，本方案统一为 400 与 TennisDiary 对齐）、`base_url` 须 http(s)、`models` 至少 1 项；`api_key` 留空 = 保留（编辑）。
- `delete_provider(id)`：若 `name == get_config_value('ai.provider')` 返回 409（被当前直选引用）。
- 删除旧的 `activate_provider` / `select_model` 端点，改为 `/api/v1/config` 的 GET（列表）/ PUT `/{key}`（覆盖）/ DELETE `/{key}`（恢复），与 TennisDiary `/api/admin/config` 行为对齐。

### 2.4 校验模型（check-models）

- `POST /api/v1/db/providers/check-models`，body：`base_url`、`api_key`、`models[]`。
- 先 `GET {base_url}/models`：200 则解析 `available`（`data[]`/`models[]` 取 `id`/`name`），逐模型 `ok = in available`；401/403 鉴权失败；否则逐模型 `POST {base_url}/chat/completions`（`max_tokens=1`）探测；超时 15s（`httpx.AsyncClient(timeout=15)`）。
- 返回 `{ ok, strategy: 'list'|'probe', available?, results: [{model, ok, message}], message? }`。

### 2.5 运行时解析统一入口

- `main.py` `/health`、`utils/llm.py` `_resolve_provider`、`utils/environment.py` `_resolve_effective_provider` 全部切到 `config_service.get_ai_config(config)`。
- DB 不可用时 try/except 降级到静态 `AppConfig`，避免推理失败。
- `db_service.get_active_provider` 删除或改为读 config 的薄封装。

### 2.6 前端

- 新增/重写 `frontend/src/components/ProviderManageModal.vue`：表格（名称/Base URL/默认模型+数量/API Key 掩码/启用/操作）+ 内联新增编辑表单（每模型一行可增删、「+ 添加模型」「校验模型」按钮、逐模型 ✓/✗、enabled 复选框），被引用项删除禁用。视觉沿用 TennisClip 暗色玻璃拟态（slate-900/950 + emerald 主色）。
- `frontend/src/lib/api.js`：id 路由 CRUD、新增 `checkProviderModels`、`getConfig`/`setConfig`/`deleteConfig`。
- `frontend/src/stores/task.js`：providers 用 `id`/`is_selected`/`default_model`；`selectProvider` 写 `ai.provider`、`selectModel` 写/恢复 `ai.model`；CRUD 按 id。
- `frontend/src/components/UploadPanel.vue`：provider 下拉 = `自定义（独立配置）` + 各启用服务商；model 下拉 = `跟随服务商默认` + 各模型。
- `frontend/src/components/HealthBar.vue`：渲染新 `/health.provider`（name/model/base_url/api_key_set/source）。
- `frontend/src/App.vue`：`onMounted` 增加 `loadConfig` 同步 `ai.provider`/`ai.model`。

## 三、实施步骤（TDD 优先）

- [ ] Step 1：新增 `config_registry.py` 与 `config_service.py`（`get_ai_config`/`mask_secret`/配置覆盖/回落），对齐 TennisDiary
- [ ] Step 2：重构 `db_models.py` 为 `ai_providers` + `SystemConfig`，新增 Alembic 迁移（建 `system_config` → 数据迁 `ai_providers`（建/拷/删旧表）→ drop `is_active`/`selected_model`）
- [ ] Step 3：改造 `db_service.py`：id 路由 CRUD、`is_selected`、掩码前3末4、删除选中 409、`list_provider_options`
- [ ] Step 4：改造 `main.py`：`/{id}` 路由、`check-models`、`/config` 端点、`/health` 走 `get_ai_config`；更新 `llm.py` 与 `environment.py` 解析
- [ ] Step 5：**后端 TDD**：先写失败测试（id 路由、掩码、is_selected、409、check-models list/probe 双路径、config 覆盖/恢复、`get_ai_config` 引用解析），再实现/修正至全绿
- [ ] Step 6：改造 `api.js` 与 `stores/task.js`：id 路由 CRUD、`checkProviderModels`、config 直选、`is_selected`
- [ ] Step 7：重写 `ProviderManageModal.vue`（表格+内联多模型表单+校验模型）并重连 `UploadPanel`/`HealthBar`/`App.vue`
- [ ] Step 8：文档 — 写本方案文档并同步 `AGENTS.md`/README，`git-commit` 原子提交并更新 `CHANGELOG.md`

> **后端 TDD 约定**：Step 1~4 的实现与 Step 5 的测试应交替进行——每实现一个函数先补失败用例（Red），跑 `uv run pytest tests/` 看到失败，再实现（Green），最后重构（Refactor）。迁移脚本（Step 2）同样需有测试覆盖数据迁移正确性。

## 四、验收标准

- `ai_providers` 表结构与 TennisDiary `ai_provider.py` 列一致（`id`/`name`/`base_url`/`api_key`/`models`/`enabled`(int)/`sort_order`/`created_at`/`updated_at`），无 `is_active`/`selected_model`。
- 激活/选模型经 `system_config`（`ai.provider`/`ai.model`），`/health`、LLM 调用、启动自检均经 `get_ai_config`，DB 不可用回落静态配置。
- 掩码为 `前3+末4`；列表项含 `is_selected`；删除被直选引用的服务商返回 409。
- `check-models` 对支持 `GET /models` 与不支持（probe）两类接口均正确返回 `results`。
- 编辑/删除路由为 `/{id}`；前端管理弹窗与 TennisDiary「管理服务商」信息结构一致，含逐模型 ✓/✗ 校验。
- 后端 `uv run pytest tests/` 全绿；日志无 f-string/%/拼接（通过 `scripts/check_logging.py`）。

## 五、风险与应对

| 风险 | 影响 | 应对方案 |
|------|------|---------|
| SQLite 不支持 DROP COLUMN / 改名 | 迁移失败 | 用「建新表→拷贝数据→删旧表」规避，不直接 ALTER |
| 掩码语义变更破坏现有断言 | 测试红 | 同步更新 `test_provider_*`/environment 期望（前3末4、is_selected、id 路由、409） |
| httpx 未依赖 | check-models 无法 import | 确认 `pyproject.toml`，缺失则加入并 `uv sync` |
| DB 抖动影响推理 | 运行时报错 | `get_ai_config` 全程 try/except 回落 `AppConfig` |
| 旧 `activate`/`select-model` 端点被前端引用 | 404 | 前端同步改为 config 直选（`/config`） |

## 六、关联文档

- [05：模型提供商多模型管理](./05-模型提供商多模型管理.md) — 本方案的上一版（provider 行内直存），本次改造的起点
- [04：前端模型选择功能](./04-前端模型选择功能.md) — 前端 provider/model 下拉基础
- [根目录 AGENTS.md](../AGENTS.md) — API 契约与编码约定（改造后需同步）
- [参考实现：TennisDiary](../reference/TennisDiary)（位于 `docs/reference`，`.gitignore` 不入库）— 表结构、service、router、管理弹窗蓝本
