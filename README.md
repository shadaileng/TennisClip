# TennisClip AI

基于**阶跃星辰 Step 3.7 Flash** 模型的网球视频 AI 高光集锦 + 动作技术分析工具。

- 上传 1–5 分钟网球训练/比赛视频，十几秒内输出 **15 秒标准高光集锦** + **结构化动作技术分析报告**
- 双部署模式：云端 API（快速上线）/ 本地私有化部署（数据不出内网）
- 全链路自动化：视频预处理 → 多模态理解 → 高光识别 → 自动剪辑 → 报告生成 → 结果交付

## 项目结构（前后端分离）

```
TennisClip/                        # 总项目
├── backend/                       # 后台（Python / FastAPI / uv 管理）
│   ├── app/
│   │   ├── cli.py                 # 命令行入口（一键处理单个视频）
│   │   ├── config.py              # 配置管理（providers 模式 + .env + 数据库）
│   │   ├── models.py              # 数据模型 / 结构化结果定义
│   │   ├── main.py                # FastAPI 服务入口 + 前端静态托管 + 数据库端点
│   │   ├── core.py                # 全链路流水线（各节点同步落库）
│   │   ├── db.py                  # 数据库引擎/会话（SQLAlchemy 多兼容层）
│   │   ├── db_models.py           # ORM 表结构（任务/输入/输出/结果/提供商/文件）
│   │   ├── services/             # preprocess / highlight / video_editor / report / db_service
│   │   └── utils/                # ffmpeg / llm / tasks / logger
│   ├── prompts/                   # 领域 Prompt 模板（网球教学知识库注入点）
│   ├── tests/                     # 单元测试
│   ├── data/                      # 统一数据目录（数据库/输入/输出/日志）
│   │   ├── sample_videos/         # 示例输入视频目录
│   │   ├── outputs/               # 处理结果输出目录（自动创建）
│   │   ├── tennisclip.db          # SQLite 数据库（自动创建）
│   │   └── app.log                # 滚动日志
│   ├── config.yaml                # 运行配置（providers + 数据库 url）
│   ├── pyproject.toml             # uv 依赖声明
│   ├── uv.lock
│   ├── .env.example
│   └── requirements.txt           # pip 镜像清单（参考）
│
├── frontend/                      # 前端（Vue 3 / Vite / Pinia / Tailwind）
│   ├── src/
│   │   ├── main.js
│   │   ├── App.vue
│   │   ├── components/           # HealthBar / UploadPanel / TaskCard / VideoPlayer / ReportView
│   │   ├── lib/api.js            # 后端 API 客户端
│   │   └── stores/task.js        # Pinia 任务状态（轮询）
│   ├── index.html
│   ├── vite.config.js            # dev 代理 /api、/health → 127.0.0.1:8000
│   ├── tailwind.config.js
│   ├── postcss.config.js
│   └── package.json
│
├── docs/                          # 文档中心（docs-manage skill 约定）
│   ├── README.md                  # 文档索引
│   ├── _template.md               # 文档创建模板
│   ├── index.md                   # VitePress 首页
│   ├── plans/01-需求分析与落地方案.md  # 完整需求分析与落地方案
│   ├── architecture/  references/  guides/
│   └── .vitepress/config.mts      # 侧边栏配置（新增文档必须同步）
├── scripts/                       # 根目录工具脚本（跨前后端）
│   └── manage_services.py         # 失管后台服务进程查询/清理（list / clean）
├── .codebuddy/skills/docs-manage  # 文档管理 skill
├── AGENTS.md                       # AI 编码代理上下文与约定
├── README.md
├── CHANGELOG.md
├── LICENSE                          # MIT 开源协议
└── .gitignore
```

## 快速开始

### 1. 启动后台（backend/）

```bash
cd backend
uv sync              # 安装依赖（自动创建 backend/.venv）
uv sync --extra dev  # 含 pytest / opencv
uv sync --extra cv   # 可选：CV 感知层（torch/ultralytics/mediapipe/transformers）
uv run python scripts/fetch_tracknet_weights.py  # 可选：一键下载转换 TrackNet 权重 → data/models/tracknet.pth
```

需系统安装 [FFMPEG](https://ffmpeg.org/)（Windows 可用 `winget install Gyan.FFmpeg`）。

### 2. 配置模型 API（OpenAI 兼容格式，providers 模式）

```bash
cd backend
copy .env.example .env   # Windows
# cp .env.example .env   # Linux/macOS
```

`.env` 填当前 provider 的 API Key（环境变量名由 `api_key_env` 指定）：

```bash
# 默认提供商：StepFun（阶跃星辰）
STEPFLASH_API_KEY=sk-xxxx
```

模型提供商在 `backend/config.yaml` 的 `llm.providers` 列表中以 OpenAI 兼容三要素声明，
通过 `llm.active_provider` 切换：

```yaml
llm:
  active_provider: default      # 切换为 openai / ollama / vllm 即可
  providers:
    - name: default
      base_url: https://api.stepfun.com/v1
      api_key_env: STEPFLASH_API_KEY
      model: step3.7-flash
    - name: openai
      base_url: https://api.openai.com/v1
      api_key_env: OPENAI_API_KEY
      model: gpt-4o-mini
    - name: ollama
      base_url: http://127.0.0.1:11434/v1
      api_key_env: OLLAMA_API_KEY
      model: step3.7-flash
```

> 该结构已落地到数据库：`ai_providers` 表（纯凭据目录，由 config.yaml 的 providers 种子导入），
> 激活服务商（`ai.provider`）与选定模型（`ai.model`）改由 `system_config` 配置 KV 覆盖，
> 运行时经 `config_service.get_ai_config()` 解析，数据库不可用时回落静态 `config.active_provider`。

未配置 API Key 时，LLM 客户端自动回退到 **Mock 模式**（返回示例结构化结果），便于无网环境联调。

### 3. 数据库（多兼容：SQLite 默认 / PostgreSQL / MySQL）

任务、输入、输出、结果快照、模型提供商、文件管理自动落库（`backend/data/tennisclip.db`）。

切到 PostgreSQL / MySQL 只需改 `backend/config.yaml` 的 `database.url`：

```yaml
# PostgreSQL（需 uv pip install "psycopg[binary]"）
database:
  url: "postgresql+psycopg://tennisclip:tennisclip@127.0.0.1:5432/tennisclip"

# MySQL（需 uv pip install pymysql）
database:
  url: "mysql+pymysql://tennisclip:tennisclip@127.0.0.1:3306/tennisclip"
```

表结构（ORM 自动建表 + **Alembic 迁移**，幂等）：

| 表 | 说明 |
|---|---|
| `tasks` | 任务记录（task_id/状态/层级/耗时/错误） |
| `task_inputs` | 输入记录（视频路径/大小/时长/分辨率/帧率） |
| `task_outputs` | 输出记录（集锦/报告文件路径/大小） |
| `task_results` | 结果快照（高光 JSON、报告 JSON 全文） |
| `ai_providers` | 模型服务商凭据目录（运行时可动态切换） |
| `system_config` | 配置 KV（ai.provider / ai.model 等覆盖） |
| `files` | 文件管理（处理涉及文件的状态跟踪） |

### 数据库迁移（Alembic）

表结构变更统一走 Alembic 迁移（`backend/alembic/`），服务启动时自动处理：

- 库已有 `alembic_version` 表 → `alembic upgrade head` 升到最新
- 旧库 / 首次（无该表）→ `create_all` 兜底建表 + `stamp head` 登记版本

手动操作（`cd backend`）：

```bash
# 表结构变更：先改 ORM 模型，再生成迁移
uv run alembic revision --autogenerate -m "变更说明"
uv run alembic upgrade head        # 应用最新
uv run alembic downgrade -1        # 回退一步
```

连接串与 ORM 元数据由 `backend/alembic/env.py` 动态解析（`DATABASE_URL` > `config.yaml`），与运行时代码同源，无需在 `alembic.ini` 写死 URL。

### 3. 启动前端（frontend/）

```bash
cd frontend
pnpm install
pnpm dev          # http://127.0.0.1:5173，Vite 代理 /api、/health → 127.0.0.1:8000
```

先启动后台（`uv run uvicorn app.main:app`），再启动前端，浏览器访问 5173 即可联调。

> **停止与清理**：服务请在各自终端 `Ctrl+C` 停止。若发现已失管的后台服务（孤儿进程、端口被占），用根目录脚本回收：
>
> ```bash> python3 scripts/manage_services.py list          # 只读查询（--json 机器可读）
> python3 scripts/manage_services.py clean --dry-run   # 预览将清理的进程，不发信号
> python3 scripts/manage_services.py clean --yes       # TERM → 校验 → 必要时 KILL → 复验
> ```

### 4. 单端口部署（前端构建后由 FastAPI 托管）

```bash
cd frontend
pnpm build        # 产物在 frontend/dist/
cd ../backend
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
# 浏览器访问 http://127.0.0.1:8000
```

`app/main.py` 检测到 `frontend/dist` 存在时，自动托管静态资源 + SPA fallback。

### 4b. 生产环境前后端分开部署

开发环境前端用 Vite dev server 代理后台；生产环境前后端分开部署，支持两种子模式：

**同源（推荐）**：nginx 托管 `frontend/dist/`，并把 `/api`、`/health` 反代到 FastAPI：

```nginx
server {
  listen 80;
  server_name tennisclip.example.com;

  root /var/www/tennisclip/dist;
  index index.html;

  location / {
    try_files $uri $uri/ /index.html;   # SPA fallback
  }

  location /api/ {
    proxy_pass http://127.0.0.1:8000;
  }
  location /health {
    proxy_pass http://127.0.0.1:8000;
  }
}
```

同源下前端无需 `VITE_API_BASE_URL`，后端无需 CORS。

**跨域**：前端独立域托管 `dist/`，后端独立域。构建时注入后端基地址：

```bash
cd frontend
VITE_API_BASE_URL=https://api.tennisclip.example.com pnpm build
```

后端 CORS 放行该源（`backend/config.yaml` 的 `cors.allowed_origins`，或环境变量 `CORS_ALLOWED_ORIGINS` 逗号分隔覆盖；默认 `*`，生产建议收紧为具体源）。

### 5. 命令行处理（可选）

```bash
cd backend
uv run python -m app.cli data/sample_videos/serve.mp4 --level intermediate
# 批量：
uv run python -m app.cli --batch data/sample_videos
```

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查（返回当前 provider/model/database） |
| POST | `/api/v1/process` | 异步提交处理任务，返回 `task_id`；支持 `file` 整体直传或 `md5` 引用已上传视频（秒传复用） |
| POST | `/api/v1/upload/check` | 两步上传第一步：MD5 预检，命中跳过上传（秒传），未命中返回分片策略与进度 |
| POST | `/api/v1/upload/chunk` | 上传单个视频分片（crc32 校验 + 定位写），支持断点续传 |
| GET | `/api/v1/upload/chunks` | 查询分片进度（ok/failed/missing），断点续传依据 |
| POST | `/api/v1/upload/complete` | 分片合并校验（整文件 MD5/size）并登记，返回 `{md5, rel_path, ext}` |
| GET | `/api/v1/tasks/{task_id}` | 查询任务状态与结果 |
| GET | `/api/v1/tasks/{task_id}/report` | 下载 JSON 报告 |
| GET | `/api/v1/tasks/{task_id}/video` | 下载高光集锦视频 |
| GET | `/api/v1/db/tasks` | 任务历史（数据库审计，`?limit=50`） |
| GET | `/api/v1/db/providers` | 模型服务商列表（数据库；含 is_selected） |
| POST | `/api/v1/db/providers/check-models` | 校验模型可用性（list / probe） |
| GET | `/api/v1/config` | 配置 KV 列表（ai.provider / ai.model 等） |
| PUT | `/api/v1/config/{key}` | 设置配置覆盖（切换服务商 / 覆盖模型） |
| DELETE | `/api/v1/config/{key}` | 删除配置覆盖（恢复默认） |

## 配置说明（backend/config.yaml）

| 键 | 默认 | 说明 |
|---|---|---|
| `llm.active_provider` | `default` | 当前生效的提供商（对应 providers 列表中的 name） |
| `llm.providers[]` | — | 提供商列表，每项含 `base_url` / `api_key_env` / `model`（OpenAI 兼容格式） |
| `llm.mock_mode` | `auto` | `auto`=无 API Key 时自动 Mock；`always`=强制 Mock；`never`=必须真实调用 |
| `database.url` | `sqlite:///./data/tennisclip.db` | 数据库连接串（SQLite/Postgres/MySQL 多兼容） |
| `video.resolution` / `fps` | `720p` / `30` | 预处理统一输出规格 |
| `highlight.target_duration` | `15` | 集锦目标时长（秒） |
| `highlight.max_segments` | `3` | 集锦最多拼接的高光片段数 |
| `queue.max_concurrent_tasks` | `2` | 批量任务并发上限（限流稳载） |
| `cors.allowed_origins` | `["*"]` | 跨域允许源列表（前后端分开部署时用，可被 `CORS_ALLOWED_ORIGINS` 覆盖） |
| `logging.level` | `INFO` | 日志级别（`DEBUG`/`INFO`/`WARNING`/`ERROR`），可被 `TENNISCLIP_LOG_LEVEL` 环境变量覆盖 |

## 后端环境变量（backend/.env）

| 变量 | 默认 | 说明 |
|---|---|---|
| `TENNISCLIP_LOG_LEVEL` | 未设（用 yaml 的 `INFO`） | 覆盖日志级别，优先级高于 `config.yaml` 的 `logging.level` |
| `TENNISCLIP_DATA_DIR` | 未设（用 yaml 的 `data`） | 覆盖数据目录（输入/输出/日志/数据库），优先级高于 `config.yaml` 的 `paths.data_dir` |
| `TENNISCLIP_ENV` | 未设（默认 `dev`） | 指定运行环境：`test` 时 `load_config()` 自动加载 `backend/.env.test`，与开发/生产配置隔离 |

> **测试环境隔离**：复制 `backend/.env.test.example` 为 `backend/.env.test`（不入库），运行 `uv run pytest` 时由 `backend/tests/conftest.py` 自动设置 `TENNISCLIP_ENV=test`，所有 `load_config()` 自动走 `.env.test`，测试数据统一落入 `backend/data_test/`，绝不触碰真实 `backend/data/`。详见 `docs/plans/03-测试环境隔离方案.md`。

## 前端环境变量（frontend/.env）

| 变量 | 默认 | 说明 |
|---|---|---|
| `VITE_API_BASE_URL` | 空（同源） | 生产跨域部署时注入后端基地址（构建时静态替换）。开发环境走 Vite 代理，无需设置；生产同源（nginx 反代）留空 |

## 验收指标（对应需求文档 4.2）

- **时效**：1–5 分钟视频端到端处理 ≤ 30s（不含模型推理网络延迟）
- **功能**：100% 输出标准 15s 集锦 + 结构化报告
- **精度**：高光回合识别准确率 ≥ 90%（需在样本集上离线评测）
- **稳定性**：连续 100 条批量处理无崩溃、无超时、无无效输出

## 隐私与部署

本地私有化部署时，将 `config.yaml` 中 `llm.active_provider` 切到 `ollama` / `vllm`，
对应 `base_url` 指向本地 OpenAI 兼容端点，`.env` 中填任意非空 Key 即可，
视频与结果全程不出本地；`backend/data/outputs/` 目录即为交付物，不上传任何第三方。

## 开源协议

本项目基于 [MIT License](LICENSE) 开源。完整许可文本见根目录 `LICENSE` 文件。

## 状态

- 后端：各服务模块已定义接口与 Mock 实现，可直接跑通全链路（Mock 模式）
- 前端：Vue 3 + Pinia + Tailwind 完整界面（上传/轮询/集锦播放/报告查看/层级选择）
- 接入真实 Step 3.7 Flash API 后，仅需在 `.env` 配 Key 并把 `active_provider` 切到对应 provider
