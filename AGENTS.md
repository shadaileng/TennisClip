# AGENTS.md

本文件为 AI 编码代理（agent）提供 TennisClip AI 项目的上下文、约定与边界，用于在此仓库中安全、一致地工作。

## 项目速览

TennisClip AI 是基于阶跃星辰 Step 3.7 Flash 的网球视频 AI 高光集锦 + 动作技术分析工具。前后端分离，全链路自动化：视频预处理 → 多模态理解 → 高光识别 → 自动剪辑 → 报告生成 → 结果交付。

## 目录结构

```
TennisClip/
├── backend/                  # Python / FastAPI / uv 管理
│   ├── app/
│   │   ├── cli.py            # 命令行入口（一键处理单个视频）
│   │   ├── config.py         # 配置管理（providers 模式 + .env + 数据库）
│   │   ├── models.py         # 数据模型 / 结构化结果定义
│   │   ├── main.py           # FastAPI 服务入口 + 前端静态托管 + 数据库端点
│   │   ├── core.py           # 全链路流水线（各节点同步落库）
│   │   ├── db.py             # 数据库引擎/会话（Alembic 迁移 + create_all 兜底）
│   │   ├── db_models.py      # ORM 表结构（Alembic autogenerate 的目标元数据）
│   │   ├── services/         # preprocess / highlight / video_editor / report / db_service
│   │   └── utils/            # ffmpeg / llm / tasks / logger
│   ├── alembic/              # 数据库迁移（env.py + versions/*.py，迁移脚本入库）
│   ├── alembic.ini           # Alembic 配置（连接串由 env.py 动态解析，不写死）
│   ├── prompts/              # 领域 Prompt 模板（网球教学知识库注入点）
│   ├── tests/                # 单元测试
│   ├── data/                 # 统一数据目录（.gitignore 整体忽略）
│   │   ├── sample_videos/    # 示例输入视频目录（视频文件 .gitignore 忽略）
│   │   ├── outputs/          # 处理结果输出目录（集锦/报告，.gitignore 忽略）
│   │   ├── tennisclip.db     # SQLite 数据库（.gitignore 忽略）
│   │   └── app.log           # 滚动日志（.gitignore 忽略）
│   ├── data_test/            # 测试数据目录（.gitignore 忽略，由 .env.test 驱动，隔离于 data/）
│   ├── .env.test.example     # 测试环境配置模板（入库）；.env.test 本地用不入库
│   ├── config.yaml           # 运行配置（providers + 数据库 url）
│   ├── pyproject.toml        # uv 依赖声明
│   └── uv.lock
├── frontend/                 # Vue 3 / Vite / Pinia / Tailwind
│   ├── src/
│   │   ├── main.js / App.vue
│   │   ├── components/       # HealthBar / UploadPanel / TaskCard / VideoPlayer / ReportView
│   │   ├── lib/api.js        # 后端 API 客户端
│   │   └── stores/task.js    # Pinia 任务状态（轮询）
│   ├── index.html / vite.config.js / tailwind.config.js / postcss.config.js
│   └── package.json
├── docs/                     # 文档中心（docs-manage skill 约定）
│   ├── README.md             # 文档索引与执行总览
│   ├── _template.md          # 文档创建模板
│   ├── index.md              # VitePress 首页
│   ├── .vitepress/config.mts # 侧边栏配置（新增文档必须同步）
│   ├── plans/  architecture/  references/  guides/
├── .codebuddy/skills/        # 项目 skills（docs-manage / git-commit）
├── package.json              # 根项目配置：VitePress 文档站（pnpm 管理依赖，docs/ 为内容根）
├── AGENTS.md                 # 本文件
├── README.md
├── CHANGELOG.md
├── LICENSE                   # MIT 开源协议
└── .gitignore
```

## 技术栈

| 层 | 技术 | 说明 |
|---|------|------|
| 后端 | Python 3.14 + FastAPI + SQLAlchemy | `uv` 管理依赖（`uv sync`） |
| 前端 | Vue 3 + Vite + Pinia + Tailwind | `pnpm` 管理依赖 |
| 部署 | Vite dev 代理（开发）/ nginx 反代（生产，可选） | 前后端可分开部署 |
| 模型 | StepFun step3.7-flash（OpenAI 兼容） | 可切换 openai / ollama / vllm / mock |
| 数据库 | SQLite（默认）/ PostgreSQL / MySQL | SQLAlchemy 多兼容层 + **Alembic 迁移**，ORM 自动建表 |
| 视频 | FFMPEG（系统依赖） | 预处理统一 720p / 30fps |
| 文档站 | VitePress（Vue 驱动静态站） | 根目录 `pnpm` 管理，`docs/` 为内容根，配置 `docs/.vitepress/config.mts` |

## 部署模式

| 模式 | 前端 | 后端 | API 基础地址 | 说明 |
|------|------|------|------|------|
| 开发 | `pnpm dev`（5173） | `uv run uvicorn app.main:app`（8000） | 同源（Vite 代理） | Vite 把 `/api`、`/health` 代理到 8000 |
| 生产（同源） | nginx 托管 `dist/` + 反代 `/api`、`/health` | FastAPI（8000） | 同源 | 单端口，无需 CORS |
| 生产（跨域） | nginx/CDN 托管 `dist/`（独立域） | FastAPI（独立域） | `VITE_API_BASE_URL` | 构建时注入，后端 CORS 放行 |


## API 契约

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查（返回嵌套 `provider` 对象 { name / model / base_url / api_key_set / source }，`source` 标识生效来源 `database` 或 `config`；`environment` 字段暴露启动四项自检明细：ffmpeg / database / provider / data_dir，status 为 ok/warn/fail） |
| POST | `/api/v1/process` | 异步提交处理任务，返回 `task_id`；支持 `file` 整体直传（按 MD5 去重落盘）或 `md5` 引用已上传视频（**秒传**：复用已落盘文件重新分析）；表单参数 `level`（beginner/intermediate/professional 分析深度，或 `all`=所有高光回合（仅剪辑拼接、不生成技术分析报告），非法值返回 400）驱动高光选择策略 |
| POST | `/api/v1/upload/check` | 两步上传第一步：MD5 预检（`{md5, size_bytes}`），命中返回 `{hit:true}`（跳过上传），未命中返回分片策略与已有会话进度（断点续传） |
| POST | `/api/v1/upload/chunk` | 上传单个分片（multipart：`md5/index/total/size_bytes/original_name/crc32/file`），片级 crc32 校验 + 定位写 `data.bin`，返回进度摘要 `{ok,failed,missing,total,total_size,chunk_size}` |
| GET | `/api/v1/upload/chunks` | 查询分片进度（`ok/failed/missing`），断点续传依据 |
| POST | `/api/v1/upload/complete` | 分片合并校验（整文件 MD5/size 二次校验防篡改），迁入 `{md5}{ext}` 并落 `uploaded_videos` 表，返回 `{md5, rel_path, ext, original_name}` |
| GET | `/api/v1/tasks/{task_id}` | 查询任务状态与结果 |
| GET | `/api/v1/tasks/{task_id}/report` | 下载 JSON 报告 |
| GET | `/api/v1/tasks/{task_id}/video` | 下载高光集锦视频 |
| GET | `/api/v1/db/tasks` | 任务历史（数据库审计，`?limit=50`） |
| GET | `/api/v1/db/providers` | 模型服务商列表（数据库；字段 id/name/base_url/**api_key(掩码)**/models/default_model/enabled/sort_order/is_selected） |
| POST | `/api/v1/db/providers` | 新增服务商（body: name/base_url/api_key/models/enabled/sort_order；重复名 409、base_url 须 http(s)、models 至少 1 项） |
| PUT | `/api/v1/db/providers/{id}` | 编辑服务商（按 id 定位；api_key 留空表示保留原值；重复名 409） |
| DELETE | `/api/v1/db/providers/{id}` | 删除服务商（被 `ai.provider` 直选引用时返回 409） |
| POST | `/api/v1/db/providers/check-models` | 校验模型可用性：body 传 `base_url`/`api_key`/`models`，返回 list（GET /models）或逐模型 probe（chat/completions）结果 |
| GET | `/api/v1/config` | 配置 KV 列表（ai.provider/ai.model/ai.api_key/ai.base_url；select 项含动态选项、secret 掩码、source 标明 db/config/env/builtin） |
| PUT | `/api/v1/config/{key}` | 设置配置覆盖（如 `ai.provider` 切换服务商、`ai.model` 覆盖模型；secret 空值=保留、等于默认值=自动删行） |
| DELETE | `/api/v1/config/{key}` | 删除配置覆盖（恢复默认值） |

模型服务商以 OpenAI 兼容三要素（`base_url` / `api_key` / `models`）在 `backend/config.yaml` 的 `llm.providers` 声明（`models` 优先，缺省由 `model` 包装为单元素列表），通过 `llm.active_provider` 切换；该结构已落地到数据库 `ai_providers` 表（纯凭据目录：`id` 主键、`name` 唯一、`enabled` 用 int、无 `is_active`/`selected_model`）。`api_key` **直接入库明文**、列表/详情接口以掩码返回（**前 3 + 末 4 位**，如 `sk-****cdef`），管理页用密码框填写；每个服务商可配多个模型（`models` JSON 列表，首项为 `default_model`）。未配置 API Key 时 LLM 客户端自动回退 **Mock 模式**（`llm.mock_mode: auto`）。

- 激活服务商与选定模型不再存于 provider 行内，而是由 `system_config` 配置 KV 表覆盖：`ai.provider`（直选生效服务商，值可为某服务商名或 `custom`）、`ai.model`（覆盖所选服务商的默认模型，空=跟随默认）；另含 `ai.api_key`/`ai.base_url` 供 `custom` 独立配置。配置 KV 子系统由 `app/config_registry.py`（最小注册表）+ `app/services/config_service.py`（`get_ai_config`/`mask_secret`/配置覆盖）封装。
- 生效服务商的唯一事实来源为配置 KV `ai.provider`：运行时 LLM 调用（`app/utils/llm.py`）、`/health` 与启动自检（`app/utils/environment.py`）均经 `config_service.get_ai_config(config)` 解析——命中启用服务商则引用其 `api_key`/`base_url`、`model` 取 `ai.model` 覆盖或 `default_model`，否则回落静态 `config.active_provider`（DB 故障返回 `None` 由调用方降级，继续回落静态配置）。原 `db_service.get_active_provider()` 已移除。
- 启动环境自检（`app/utils/environment.py` 的 `run_startup_checks`）在 FastAPI 启动时执行一次，结果存入 `app.state.environment_checks` 并映射到 `/health` 的 `environment` 字段；任一项不通过仅 `logger.warning`、不阻断启动。`environment` 含四项：`ffmpeg`（探测 ffmpeg/ffprobe，ok/fail）、`database`（按 `config.database.url` 建连并执行 `SELECT 1` ping，ok/fail）、`provider`（优先 DB `ai.provider` 配置引用，关键字段缺失/无法解析判 fail，无 API Key 且 `mock_mode=auto` 判 warn、否则 fail）、`data_dir`（目录可创建且可写，ok/fail）。各 `detail` 不回显凭据：数据库连接串 `@` 前部分（用户名/密码）已剥离，API Key 仅以布尔 `api_key_set` 暴露。

## 编码约定

### 后端（Python）
- 依赖统一用 `uv`（`uv sync` / `uv run`），勿直接 `pip install` 进系统环境。
- 配置读取走 `app/config.py`（`AppConfig`），providers 模式；勿在业务代码硬编码 API Key 或连接串。
- 环境切换：`TENNISCLIP_ENV=test` 时 `load_config()` 自动加载 `backend/.env.test`（测试隔离），否则加载 `backend/.env`；保留 `env_file` 显式参数。测试数据统一落入 `backend/data_test/`，不触碰真实 `backend/data/`。
- 数据库操作经 `app/db.py`（引擎/会话）+ `app/db_models.py`（ORM）；表结构变更改 ORM 模型后，用 **Alembic** 生成迁移，勿手写 DDL。
- **数据库迁移（Alembic）**：
  - 服务启动时 `init_db` 自动处理：库已有 `alembic_version` 表则 `alembic upgrade head`；旧库（无该表）则 `create_all` 兜底 + `stamp head`。
  - 手动操作：`cd backend`，`uv run alembic revision --autogenerate -m "变更说明"`（生成新迁移）→ `uv run alembic upgrade head`（应用）→ `uv run alembic downgrade -1`（回退）。
  - 连接串与 ORM 元数据由 `alembic/env.py` 动态解析（`DATABASE_URL` > `config.yaml`），与运行时同源；勿在 `alembic.ini` 写死 URL。
  - 新增表/列/索引：改 `app/db_models.py` 的 ORM → 跑 `alembic revision --autogenerate` → 审查生成的 `alembic/versions/*.py` 后 `upgrade head`。
- 全链路逻辑集中在 `app/core.py`，各节点结果同步落库；新增节点保持该契约。
- Prompt 模板集中在 `prompts/`（网球教学知识库注入点），勿散落在 service 内。
- 视频处理依赖系统 FFMPEG，新增调用走 `app/utils/ffmpeg.py` 封装。
- 日志统一走 `app/utils/logger.py`（基于 **loguru**），勿直接 `print`。
  - **获取 logger**：`from app.utils.logger import get_logger; logger = get_logger(__name__)`，返回已绑定模块名的 loguru `Logger`，现有 12 处调用点无需改动。
  - **统一格式规范**：`时间 | 级别 | 模块:函数:行号 - 消息`（时间毫秒精度 `YYYY-MM-DD HH:mm:ss.SSS`，级别按 `{level: <8}` 右补位）。
    - 控制台 sink（stderr）带颜色标签；文件 sink（纯文本，无 ANSI 转义，便于 grep/归档）。
  - **日志级别来源**：环境变量 `TENNISCLIP_LOG_LEVEL` > `config.yaml` 的 `logging.level`（经 `AppConfig.logging_level`）> 默认 `INFO`。
  - **日志落盘**：`backend/data/app.log`，滚动 `rotation="10 MB"`、`retention="7 days"`、`compression="zip"`；已被 `.gitignore` 忽略，不入库。
  - **uvicorn / FastAPI 日志**：经 `InterceptHandler` 统一接管，全链路同一套格式，无需额外配置。
  - **带参调用规范（必须遵守）**：
    - 用 loguru 延迟求值 `{}` 占位符，禁止 f-string / `%` / 字符串拼接（未达级别不格式化，省开销）：
      `logger.info("开始处理视频 path={} task_id={}", video_path, task_id)`
    - 结构化上下文用 `logger.bind(key=value)` 注入，可在格式中以 `{extra[key]}` 引用：
      `logger.bind(task_id=task_id).error("处理失败：{}", exc)`
  - **自动校验（检验标准）**：`backend/scripts/check_logging.py` 用 AST 静态扫描强制上述规范（禁止 `%` 风格 / f-string / 字符串拼接，要求 `{}` 占位符）；`uv run python backend/scripts/check_logging.py` 检出违规时退出码为 1。该检查已被 `tests/test_logging_convention.py` 纳入 `uv run pytest` 回归拦截，建议在 CI 中加入此脚本。
  - **JSON 备选**：本项目为本地单机工具，默认纯文本；若后续接入日志聚合系统，可将文件 sink 改为 `serialize=True` 输出 JSON。

### 前端（Vue 3）
- 组件放 `frontend/src/components/`，页面级状态走 Pinia store（`stores/`）。
- 与后端交互统一经 `lib/api.js`，组件内不直接写 fetch。
- 样式使用 Tailwind；构建产物 `frontend/dist/`（.gitignore 忽略），单端口部署时由 FastAPI 托管。
- dev 代理：`vite.config.js` 将 `/api`、`/health` 转发到 `127.0.0.1:8000`。
- **API 基础地址**：`lib/api.js` 读取 `import.meta.env.VITE_API_BASE_URL`（构建时静态替换）。
  - 开发环境不设置 → 走 Vite 代理，同源。
  - 生产跨域部署：`VITE_API_BASE_URL=https://api.example.com pnpm build` 注入后端基地址。
  - 生产同源（nginx 反代）：留空即可。

## 部署与配置

### 开发环境
前端 `pnpm dev`（5173，Vite 代理到 8000）+ 后端 `uv run uvicorn app.main:app`（8000），同源，无需 CORS。

### 生产环境（前后端分开部署）
- **同源（推荐）**：nginx 托管 `frontend/dist/`，并将 `/api`、`/health` 反代到 FastAPI；前端无需 `VITE_API_BASE_URL`，后端无需 CORS。
- **跨域**：前端 `VITE_API_BASE_URL=https://api.example.com pnpm build` 注入后端基地址；后端 CORS 放行该源。
  - CORS 配置：`backend/config.yaml` 的 `cors.allowed_origins`（默认 `["*"]`），可被环境变量 `CORS_ALLOWED_ORIGINS`（逗号分隔）覆盖，优先级更高。
  - 生产建议收紧为具体源列表，避免 `*`。

### 环境变量（前端）
`frontend/.env` / `frontend/.env.production`（gitignore 忽略，参考 `frontend/.env.example`）：

| 变量 | 默认 | 说明 |
|------|------|------|
| `VITE_API_BASE_URL` | 空（同源） | 生产跨域部署时注入后端基地址，构建时静态替换 |

### 环境变量（后端，参考 `backend/.env.example`）

| 变量 | 默认 | 说明 |
|------|------|------|
| `CORS_ALLOWED_ORIGINS` | 未设（用 yaml 的 `*`） | 逗号分隔的允许源列表，覆盖 yaml |
| `DATABASE_URL` | yaml 的 SQLite | 数据库连接串（多兼容） |
| `TENNISCLIP_DATA_DIR` | 未设（用 yaml 的 `data`） | 数据目录（输入/输出/日志/数据库），优先级高于 `config.yaml` 的 `paths.data_dir` |
| `TENNISCLIP_ENV` | 未设（默认 `dev`） | 运行环境：`test` 时 `load_config()` 自动加载 `backend/.env.test`，测试数据落入 `backend/data_test/`，与开发/生产隔离 |
| `TENNISCLIP_LOG_LEVEL` | 未设（用 yaml 的 `INFO`） | 日志级别，优先级高于 `config.yaml` 的 `logging.level` |

## 文档约定（docs-manage skill）

- 文档中心在 `docs/`，遵循 `.codebuddy/skills/docs-manage/SKILL.md`。
- 命名：`{NN}-{中文标题}.md`（编号各子目录独立从 01 递增，无空格）。
- 状态：📋待执行 / 🚧进行中 / 🏁已完成 / ⏳已归档。
- **新增文档后必须**同步 VitePress 侧边栏并运行校验：
  `python .codebuddy/skills/docs-manage/scripts/sync_sidebar.py`
- 参考代码目录 `docs/reference/` 不入库（.gitignore）。

## 提交约定（git-commit skill）

- 遵循 `.codebuddy/skills/git-commit/SKILL.md`：Conventional Commits，描述用中文（≤50 字）。
- `feat` / `fix` 提交后须按 type 自动更新 `CHANGELOG.md`（Keep a Changelog，版本倒序，章节固定顺序 Added→Changed→Deprecated→Removed→Fixed→Security）。
- 功能/部署/环境变量变更同步更新 `README.md`；项目结构/API 契约/编码规范变更同步更新本文件 `AGENTS.md`。
- 禁止 `git add .` / `git add -A` 全量暂存；只暂存本次逻辑涉及的具体文件。
- 自动提交后**不推送远程**；一个提交只做一件事（原子提交）。

## 构建 / 运行 / 测试

```bash
# 后端
cd backend
uv sync                      # 安装依赖
uv run uvicorn app.main:app  # 启动 API（8000）
uv run pytest tests/         # 运行单元测试
uv run python -m app.cli data/sample_videos/serve.mp4   # 命令行处理单视频

# 前端
cd frontend
pnpm install
pnpm dev                     # http://127.0.0.1:5173（代理到 8000）
pnpm build                   # 产物 dist/，单端口部署由 FastAPI 托管

# 文档站（根目录，VitePress；docs/ 为内容根）
pnpm install                 # 根目录安装 vitepress 等依赖
pnpm run docs:dev            # http://127.0.0.1:5173 本地预览文档（与前端 dev 同端口，按需错开）
pnpm run docs:build          # 产物 docs/.vitepress/dist/（.gitignore 忽略）
pnpm run docs:preview        # 预览构建产物
```

需系统安装 [FFMPEG](https://ffmpeg.org/)（Windows：`winget install Gyan.FFmpeg`）。

## 边界与注意事项

- `.gitignore` 已忽略：`backend/.venv/`、`backend/data/`（数据库/输入/输出/日志整体忽略）、`backend/data_test/`（测试数据目录，隔离于 data/）、`backend/.env.test`（测试配置，本地用不入库）、`backend/test_roundtrip.db`（根目录遗留测试库）、`frontend/node_modules/`、`frontend/dist/`、`__pycache__/`。
- 提交时勿将生成数据库或视频文件加入版本控制。
- 本仓库已采用 MIT 协议（`LICENSE`），修改协议或版权署名需谨慎并同步 README。
- 验收指标：1–5 分钟视频端到端 ≤ 30s（不含模型推理网络延迟）；高光回合识别准确率 ≥ 90%（样本集离线评测）；连续 100 条批量无崩溃。
