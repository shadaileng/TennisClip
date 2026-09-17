---
name: 对齐TennisDiary服务商管理（配置KV+ai_providers+校验模型）
overview: 完全对齐 TennisDiary 的 AI 服务商管理：后端引入 config KV 子系统（system_config 表 + config_registry + config_service），将 TennisClip 的 model_providers 表重构为 ai_providers（id 主键、enabled 用 int、去掉 is_active/selected_model），激活服务商与选定模型改为配置覆盖（ai.provider / ai.model）；新增 check-models 探测接口；编辑/删除路由改按整数 id；前端接入服务商管理弹窗（表格+内联表单+逐模型 ✓/✗ 校验）与配置直选下拉。
design:
  styleKeywords:
    - Dark Glassmorphism
    - Emerald Accent
    - Card Table
    - Per-row Model Validation
    - Minimal Micro-interaction
  fontSystem:
    fontFamily: PingFang SC
    heading:
      size: 18px
      weight: 600
    subheading:
      size: 14px
      weight: 500
    body:
      size: 12px
      weight: 400
  colorSystem:
    primary:
      - "#10b981"
      - "#34d399"
      - "#059669"
    background:
      - "#0f172a"
      - "#020617"
      - "#1e293b"
    text:
      - "#f1f5f9"
      - "#cbd5e1"
      - "#64748b"
    functional:
      - "#10b981"
      - "#ef4444"
      - "#f59e0b"
todos:
  - id: backend-config-kv
    content: 新增 config_registry.py 与 config_service.py（get_ai_config/mask_secret/配置覆盖），对齐 TennisDiary
    status: completed
  - id: backend-orm-migration
    content: 重构 db_models.py 为 ai_providers + SystemConfig，并新增 Alembic 迁移（建表/数据迁移/删旧表）
    status: completed
    dependencies:
      - backend-config-kv
  - id: backend-provider-service
    content: 改造 db_service.py：id 路由 CRUD、is_selected、掩码前3末4、删除选中 409、list_provider_options
    status: completed
    dependencies:
      - backend-orm-migration
  - id: backend-endpoints
    content: 改造 main.py：/{id} 路由、check-models、/config 端点、/health 走 get_ai_config，更新 llm.py 与 environment.py 解析
    status: completed
    dependencies:
      - backend-provider-service
  - id: backend-tests
    content: 更新并新增后端测试（id 路由、掩码、is_selected、409、check-models、config 覆盖、get_ai_config）
    status: completed
    dependencies:
      - backend-endpoints
  - id: frontend-api-store
    content: 改造 api.js 与 stores/task.js：id 路由 CRUD、checkProviderModels、config 直选、is_selected
    status: completed
    dependencies:
      - backend-endpoints
  - id: frontend-modal-panel
    content: 重写 ProviderManageModal.vue（表格+内联多模型表单+校验模型）并重连 UploadPanel 与 HealthBar/App.vue
    status: completed
    dependencies:
      - frontend-api-store
  - id: docs-and-commit
    content: 用 [skill:docs-manage] 写方案文档并同步 AGENTS.md/README，用 [skill:git-commit] 原子提交并更新 CHANGELOG
    status: completed
    dependencies:
      - frontend-modal-panel
---

## User Requirements
完全对齐 /workspace/docs/reference/TennisDiary 项目的「管理服务商弹窗」与后台实现：前端弹窗 UI、后端表结构与处理全部参考 TennisDiary。

## Product Overview
将 TennisClip 的模型服务商管理从「provider 表内 is_active/selected_model 直存」改造为 TennisDiary 的「配置 KV 驱动」模式：新增 system_config 配置表与 config_service，激活服务商（ai.provider）与选定模型（ai.model）改为配置覆盖；provider 表重构为 ai_providers（id 主键、enabled 用 int、去除 is_active/selected_model）；新增「校验模型」探测接口；编辑/删除路由改按整数 id；前端接入与 TennisDiary 一致的服务商管理弹窗（表格 + 内联多模型表单 + 逐模型 ✓/✗ 校验），上传面板 provider/model 下拉改为配置直选。

## Core Features
- 服务商管理弹窗：表格展示（名称/Base URL/默认模型+数量/API Key 掩码/启用/操作），内联新增或编辑表单（名称、Base URL、API Key 密码框、每模型一行可增删、启用开关），删除被当前直选引用时拦截。
- 逐模型校验：弹窗「校验模型」按钮调用后端 check-models（list GET /models 或逐模型 chat/completions 探测，15s 超时），前端逐模型显示可用/不可用及服务商可用模型清单。
- 配置 KV 子系统：system_config 表（key/value）+ config_registry 注册 ai.provider/ai.model/ai.api_key/ai.base_url；get_ai_config 按 ai.provider 引用解析（选中则引用条目，否则回落独立配置；model = ai.model 覆盖或 default_model）。
- 按 id 的 CRUD：GET/POST /api/v1/db/providers，PUT/DELETE /api/v1/db/providers/{id}；掩码改为「前3+末4」；is_selected 由 ai.provider 计算；删除选中项返回 409。
- 配置直选接口：GET /api/v1/config 列表、PUT/DELETE /api/v1/config/{key} 覆盖/恢复；上传面板 provider 下拉（custom + 各服务商）、model 下拉（跟随默认 + 各模型）写入 ai.provider/ai.model。
- 运行时统一解析：/health、启动自检、LLM 调用均经 get_ai_config，DB 不可用时回退静态 config。


## Tech Stack
- 后端：Python 3.14 + FastAPI + SQLAlchemy（Alembic 迁移），沿用项目 uv 依赖管理；check-models 探测使用 httpx（若未依赖需加入 pyproject.toml）。
- 前端：Vue 3 + Vite + Pinia + Tailwind CSS（沿用现有栈，无额外组件库），弹窗与下拉复用现有 store/api 分层。
- 数据库：SQLite 默认（多兼容），新增 system_config 表与 ai_providers 表重构，经 Alembic autogenerate 审查后升级。

## Implementation Approach
**核心策略**：以 TennisDiary 为蓝本做一次「配置驱动」重构——把「谁是激活服务商、用哪个模型」从 provider 行内列（is_active/selected_model）抽离到独立的 system_config KV，provider 表退化为纯「凭据目录」（与 TennisDiary ai_providers 完全一致）。运行时统一入口 `get_ai_config()`：读 ai.provider，命中启用服务商则引用其 api_key/base_url，model 取 ai.model 覆盖或 default_model，否则回落静态 config。

**关键技术决策**：
1. 表改名 `model_providers → ai_providers` 并对齐列（id PK、name unique、base_url、api_key Text、models JSONList、enabled Integer、sort_order、created_at Float、updated_at DateTime；删除 is_active/selected_model）。保留 JSONList TypeDecorator（已实现，与 TennisDiary 一致）。
2. 激活/选模型改配置 KV：新增 `config_registry.py`（最小注册 ai.provider/ai.model/ai.api_key/ai.base_url，默认值取自 AppConfig）+ `config_service.py`（get_config_value/set_config_value/delete_config_value/get_ai_config/mask_secret）。ai.provider 默认值设为 config.llm.active_provider，使首启动即有选中项。
3. 掩码统一为 TennisDiary 语义：空→''、len<=8→'****'、否则 `{前3}****{末4}`，替换现有 `mask_secret`（首尾各4位）。
4. 路由改整数 id：PUT/DELETE 改用 `{provider_id}`，前端弹窗/store/api 全部按 id 引用；删除时若 `is_selected`（name==get_config_value('ai.provider')）则 409。
5. 删除旧的 `activate_provider`/`select_model` 端点，改为 `/api/v1/config` 的 GET/PUT/DELETE（键 ai.provider、ai.model），与 TennisDiary `/api/admin/config` 行为对齐。
6. check-models：POST /api/v1/db/providers/check-models，先 GET {base_url}/models 解析 available（data[]/models[] 取 id/name），命中则逐模型 ok；401/403 鉴权失败；否则逐模型 POST chat/completions max_tokens=1 探测；超时 15s。

**性能与可靠性**：provider 列表/配置解析为单次 DB 查询，O(n) 轻量；check-models 为外部网络调用，设 15s 硬超时并 try/except 包裹，失败返回 ok=False 不阻塞主流程；运行时解析全部 try/except 降级到静态 config，避免 DB 抖动导致推理失败。日志严格沿用 loguru `{}` 占位符（scripts/check_logging.py 回归拦截），不回显凭据。

**避免技术债**：复用现有 JSONList、db_service.session()、AppConfig 默认值与 alembic 动态 env；不引入 TennisDiary 的权限/审计/分类体系，仅搬「provider + config KV」两块必要逻辑，保持 TennisClip 单机工具的最小面。

## Implementation Notes
- 迁移：新增 Alembic 版本，先建 system_config，再把 model_providers 数据迁移到 ai_providers（建新表→拷贝 name/base_url/api_key/models/enabled(转 int)/sort_order/updated_at、created_at 置 0→删旧表），规避 SQLite DROP COLUMN 兼容性问题；改完 ORM 后先 `alembic revision --autogenerate` 审查。
- 运行时调用点：main.py `/health`、`utils/llm.py _resolve_provider`、`utils/environment.py _resolve_effective_provider` 全部切到 `config_service.get_ai_config(config)`；保留 `db_service.get_active_provider` 删除或改为读 config 的薄封装。
- 掩码变更会影响现有 provider 测试断言，需同步更新 test_provider_crud/api/environment 的期望（前3末4、is_selected、id 路由、删除选中 409）。
- httpx 依赖：先确认 `backend/pyproject.toml` 是否已含 httpx，缺失则加入并 `uv sync`；check-models 用 `httpx.AsyncClient(timeout=15)`。
- 测试隔离：沿用 `TENNISCLIP_ENV=test` + data_test/，不触碰 data/。
- 文档：新增 docs/plans 方案文档并同步 AGENTS.md API 契约与 README；提交遵循 git-commit 原子提交 + CHANGELOG。

## Architecture Design
```mermaid
flowchart TD
  A[前端弹窗/上传面板] -->|api| B[FastAPI 端点]
  B --> C{操作类型}
  C -->|CRUD 服务商| D[ai_provider_service: ai_providers 表]
  C -->|校验模型| E[check-models: httpx 探测 base_url]
  C -->|切换/选模型| F[config_service: system_config 表 ai.provider/ai.model]
  D -->|is_selected 计算| F
  G[/health] --> H[get_ai_config]
  I[LLM 调用] --> H
  J[启动自检] --> H
  H -->|ai.provider 命中| D
  H -->|未命中/custom| K[AppConfig 静态兜底]
```

## Directory Structure
```
backend/
├── app/
│   ├── core/config_registry.py      # [NEW] 配置项注册表：ai.provider(select)/ai.model/ai.api_key(secret)/ai.base_url(url)，默认值取自 AppConfig
│   ├── services/config_service.py   # [NEW] get_config_value/set_config_value/delete_config_value/get_ai_config/mask_secret/list_provider_options 封装
│   ├── db_models.py                 # [MODIFY] ModelProvider→AiProvider(ai_providers 表：去 is_active/selected_model、enabled 改 Integer、created_at Float、default_model 属性)；新增 SystemConfig KV 模型；_seed 改为写 ai_providers 且 ai.provider 默认取 active_provider
│   ├── services/db_service.py       # [MODIFY] 服务商函数改 AiProvider+id 路由：list_providers(含 is_selected)、create_provider、update_provider(id)、delete_provider(id,选中拦截409)、get_provider_by_id/name、list_provider_options；mask_secret 改为前3末4；移除 activate_provider/select_model/get_active_provider(改读 config)
│   ├── main.py                      # [MODIFY] /api/v1/db/providers GET/POST、PUT/DELETE /{id}、POST /check-models；新增 GET/PUT/DELETE /api/v1/config；/health 改用 get_ai_config；删除 activate/select-model 端点
│   ├── utils/llm.py                 # [MODIFY] _resolve_provider 改为调用 config_service.get_ai_config，返回 (name,base_url,model)
│   ├── utils/environment.py         # [MODIFY] _resolve_effective_provider 改为 get_ai_config，保持 fail/warn/ok 分级
│   └── db.py                        # [MODIFY] 确保 SystemConfig/AiProvider 被 import 以纳入 metadata
├── alembic/versions/
│   └── <new>_ai_providers_and_config.py  # [NEW] 建 system_config；model_providers 数据迁到 ai_providers(建/拷/删)；Drop is_active/selected_model
├── pyproject.toml                   # [MODIFY] 缺 httpx 时加入并 uv sync
└── tests/
    ├── test_provider_crud.py        # [MODIFY] 适配 id 路由、is_selected、掩码前3末4、删除选中 409、get_provider_by_id
    ├── test_provider_api.py         # [MODIFY] 端点改为 /{id}、新增 /check-models 与 /config 用例
    ├── test_environment.py          # [MODIFY] check_provider 走 get_ai_config 断言
    ├── test_pipeline.py             # [MODIFY] 推理走 config 解析的回归
    └── test_config_provider.py      # [NEW] config 覆盖/恢复、get_ai_config 引用解析、check-models list/probe 双路径

frontend/
├── src/lib/api.js                   # [MODIFY] 改 id 路由 CRUD；新增 checkProviderModels、getConfig/setConfig/deleteConfig
├── src/stores/task.js              # [MODIFY] providers 用 id/is_selected/default_model；新增/改造 selectProvider(写 ai.provider)、selectModel(写/恢复 ai.model)；CRUD 按 id
├── src/components/ProviderManageModal.vue  # [MODIFY] 重写为 TennisDiary 弹窗：表格+内联表单(每模型行增删、校验模型按钮、逐模型✓✗、enabled、删除引用拦截)，按 id 操作
├── src/components/UploadPanel.vue   # [MODIFY] provider 下拉=custom+服务商、model 下拉=跟随默认+各模型；改调 store.selectProvider/selectModel
├── src/components/HealthBar.vue     # [MODIFY] 渲染新 /health.provider(name/model/base_url/api_key_set/source)
└── src/App.vue                      # [MODIFY] onMounted 增加 loadConfig 同步 ai.provider/ai.model

docs/
├── plans/<new>-服务商管理对齐方案.md   # [NEW] 方案文档(docs-manage)
├── AGENTS.md                        # [MODIFY] API 契约(端点/字段/掩码/409)与约定更新
├── README.md                        # [MODIFY] 功能/环境变量说明同步
└── CHANGELOG.md                     # [MODIFY] feat 章节(git-commit 自动更新)
```

## Key Code Structures
```python
# app/core/config_registry.py（最小注册）
class ConfigItem:
    key: str; category: str; label: str; description: str
    value_type: str   # 'str' | 'secret' | 'url' | 'select'
    editable: bool; default: str; env_key: str | None; options: list[str] | None

# app/services/config_service.py 运行时解析
@dataclass(frozen=True)
class AIConfig:
    api_key: str; base_url: str; model: str; provider: str = "custom"

def get_ai_config(db, config: AppConfig) -> AIConfig:
    """ai.provider 命中启用服务商→引用其条目；否则回落 AppConfig；model=ai.model 覆盖或 default_model"""

# app/services/db_service.py 列表项形态
def _build_provider_item(p, selected_name: str) -> dict:
    # id, name, base_url, api_key(掩码), models, default_model,
    # enabled(bool), sort_order, is_selected=(p.name==selected_name), updated_at
```


## Design Style
沿用 TennisClip 现有暗色玻璃拟态风格（slate-900/950 背景 + emerald 主色 + 圆角/模糊边框），整体对齐 TennisDiary「管理服务商弹窗」的信息结构，不做浅色改造。弹窗为居中浮层，内部可滚动；上半部服务商表格，下半部内联新增/编辑表单。上传面板 provider/model 两块保持现有卡片式直选下拉，仅改变数据来源与交互（写入配置而非切换 is_active）。

## 页面/弹窗区块设计（ProviderManageModal）
1. 弹窗头部：标题「模型服务商管理」+ 右上角关闭按钮（✕），与现有 header 视觉一致。
2. 说明条：小字提示「手动维护 OpenAI 兼容服务商；被当前直选引用的不可删除」。
3. 服务商表格：列=名称（is_selected 时显示「当前」emerald 徽标）、Base URL（截断）、模型（默认模型 +「等 N 个」）、API Key（掩码或「—」）、启用（绿/灰）、操作（编辑 / 删除，删除二次确认且引用项禁用）。空态提示「暂无服务商」。
4. 新增按钮：表格右上「+ 新增服务商」emerald 按钮。
5. 内联新增/编辑表单：名称*、模型*（每行一模型可删除「删」+「默认」徽标；「+ 添加模型」虚线按钮；「校验模型」emerald 按钮；逐模型 ✓可用/✗不可用标记；校验结果区展示服务商可用模型清单或探测明细）、Base URL*、API Key（密码框，编辑留空保留）、启用复选框、底部「取消 / 保存」按钮（保存中禁用态）。
6. 删除拦截：被 ai.provider 引用时按钮禁用并 tooltip 提示先切换。

## 上传面板联动（UploadPanel）
- 服务商直选：下拉项 =「自定义（独立配置）」+ 各启用服务商；选中即写入 ai.provider；当前项显示「引用生效」徽标。
- 模型覆盖：下拉项 =「跟随服务商默认」+ 该服务商各模型；选中写入 ai.model，空值恢复默认（删除覆盖）。

## 交互与动效
沿用现有 transition（hover 边框转 emerald、按钮 disabled:opacity-60、focus:border-emerald-400）；新增校验模型时按钮 loading 态与逐模型实时 ✓/✗ 微标记；弹窗淡入 backdrop-blur。响应式：弹窗在窄屏 max-w-2xl 内纵向堆叠，表单 grid 在 sm 以上两列。

## Agent Extensions
### Skill
- **docs-manage**
  - Purpose: 按 docs/ 约定创建方案文档（docs/plans/），同步 AGENTS.md API 契约与 README，并更新 VitePress 侧边栏。
  - Expected outcome: 生成对齐方案 markdown 并同步索引，保证文档与代码一致。
- **git-commit**
  - Purpose: 按 Conventional Commits 原子提交本次「配置驱动服务商管理」改动，并自动更新 CHANGELOG.md 的 feat 章节。
  - Expected outcome: 拆分后端/前端/文档为独立原子提交，CHANGELOG 记录新增 check-models 与配置直选能力。
