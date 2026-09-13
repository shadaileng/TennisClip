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
│   │   ├── db.py             # 数据库引擎/会话（SQLAlchemy 多兼容层）
│   │   ├── db_models.py      # ORM 表结构
│   │   ├── services/         # preprocess / highlight / video_editor / report / db_service
│   │   └── utils/            # ffmpeg / llm / tasks / logger
│   ├── prompts/              # 领域 Prompt 模板（网球教学知识库注入点）
│   ├── tests/                # 单元测试
│   ├── data/                 # SQLite 数据库文件（自动创建，.gitignore 忽略）
│   ├── sample_videos/        # 示例输入视频目录（视频文件 .gitignore 忽略）
│   ├── outputs/              # 处理结果输出目录（.gitignore 忽略）
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
| 模型 | StepFun step3.7-flash（OpenAI 兼容） | 可切换 openai / ollama / vllm / mock |
| 数据库 | SQLite（默认）/ PostgreSQL / MySQL | SQLAlchemy 多兼容层，ORM 自动建表 |
| 视频 | FFMPEG（系统依赖） | 预处理统一 720p / 30fps |

## API 契约

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查（返回当前 provider / model / database） |
| POST | `/api/v1/process` | 上传视频，异步提交处理任务，返回 `task_id` |
| GET | `/api/v1/tasks/{task_id}` | 查询任务状态与结果 |
| GET | `/api/v1/tasks/{task_id}/report` | 下载 JSON 报告 |
| GET | `/api/v1/tasks/{task_id}/video` | 下载高光集锦视频 |
| GET | `/api/v1/db/tasks` | 任务历史（数据库审计，`?limit=50`） |
| GET | `/api/v1/db/providers` | 模型提供商列表（数据库） |
| POST | `/api/v1/db/providers/{name}/activate` | 切换当前生效的模型提供商 |

模型提供商以 OpenAI 兼容三要素（`base_url` / `api_key_env` / `model`）在 `backend/config.yaml` 的 `llm.providers` 声明，通过 `llm.active_provider` 切换；该结构已落地到数据库 `model_providers` 表，支持运行时动态切换。未配置 API Key 时 LLM 客户端自动回退 **Mock 模式**（`llm.mock_mode: auto`）。

## 编码约定

### 后端（Python）
- 依赖统一用 `uv`（`uv sync` / `uv run`），勿直接 `pip install` 进系统环境。
- 配置读取走 `app/config.py`（`AppConfig`），providers 模式；勿在业务代码硬编码 API Key 或连接串。
- 数据库操作经 `app/db.py`（引擎/会话）+ `app/db_models.py`（ORM）；表结构变更改 ORM 并由其幂等建表，勿手写 DDL。
- 全链路逻辑集中在 `app/core.py`，各节点结果同步落库；新增节点保持该契约。
- Prompt 模板集中在 `prompts/`（网球教学知识库注入点），勿散落在 service 内。
- 视频处理依赖系统 FFMPEG，新增调用走 `app/utils/ffmpeg.py` 封装。
- 日志统一走 `app/utils/logger.py`，勿直接 `print`。

### 前端（Vue 3）
- 组件放 `frontend/src/components/`，页面级状态走 Pinia store（`stores/`）。
- 与后端交互统一经 `lib/api.js`，组件内不直接写 fetch。
- 样式使用 Tailwind；构建产物 `frontend/dist/`（.gitignore 忽略），单端口部署时由 FastAPI 托管。
- dev 代理：`vite.config.js` 将 `/api`、`/health` 转发到 `127.0.0.1:8000`。

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
uv run python -m app.cli sample_videos/serve.mp4   # 命令行处理单视频

# 前端
cd frontend
pnpm install
pnpm dev                     # http://127.0.0.1:5173（代理到 8000）
pnpm build                   # 产物 dist/，单端口部署由 FastAPI 托管
```

需系统安装 [FFMPEG](https://ffmpeg.org/)（Windows：`winget install Gyan.FFmpeg`）。

## 边界与注意事项

- `.gitignore` 已忽略：`backend/.venv/`、`backend/outputs/`、`backend/data/*.db`、`backend/*.db`（含测试库 `test_roundtrip.db`）、视频文件、`frontend/node_modules/`、`frontend/dist/`、`__pycache__/`。
- 提交时勿将生成数据库或视频文件加入版本控制。
- 本仓库已采用 MIT 协议（`LICENSE`），修改协议或版权署名需谨慎并同步 README。
- 验收指标：1–5 分钟视频端到端 ≤ 30s（不含模型推理网络延迟）；高光回合识别准确率 ≥ 90%（样本集离线评测）；连续 100 条批量无崩溃。
