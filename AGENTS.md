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
│   │   ├── config_registry.py # 配置项注册表（最小集）
│   │   ├── services/         # preprocess / highlight / video_editor / report / db_service / workflow_service / config_service
│   │   ├── routers/          # upload / workflows（FastAPI 路由）
│   │   ├── workflow/         # 可编排工作流系统
│   │   │   ├── spec.py       # 节点契约：端口类型、参数 Schema、注册表
│   │   │   ├── graph.py      # 图结构：节点 + 边 + 校验（R1~R10）+ Kahn 拓扑排序
│   │   │   ├── executor.py   # 执行器：Kahn 分层并行 + Context 传值 + 声明式落库 + 产物投影
│   │   │   ├── presets.py    # 预置编译器：compile_from_legacy / compile_from_config
│   │   │   └── nodes/        # 16 个内置节点（input.video / preprocess.transcode / ...）
│   │   └── utils/            # ffmpeg / llm / tasks / logger / media_strategies（视频理解策略）/ cv_*（CV 感知层薄封装）
│   ├── alembic/              # 数据库迁移（env.py + versions/*.py，迁移脚本入库）
│   ├── alembic.ini           # Alembic 配置（连接串由 env.py 动态解析，不写死）
│   ├── prompts/              # 领域 Prompt 模板（网球教学知识库注入点）
│   ├── tests/                # 单元测试
│   ├── scripts/              # 工具脚本（check_logging 日志规范检查 / fetch_tracknet_weights 权重一键下载转换）
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
│   │   ├── components/       # HealthBar / UploadPanel / TaskCard / VideoPlayer / ReportView / ProviderManageModal / WorkflowPanel / WorkflowCanvas / TaskHistory
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
├── scripts/                  # 根目录工具脚本（跨前后端）
│   └── manage_services.py    # 失管后台服务进程查询/清理（list / clean，纯标准库 + /proc）
├── kaggle/                   # Kaggle GPU 验证包（方案 14 · Step 2/6；代码零分叉）
│   ├── verify.py             # 16 节点 executor 完整移植入口（PureExecutor，落 JSON 不连 DB）
│   ├── build_dataset.ps1     # 一键打包 backend/{app,prompts} → app.tar.gz + 权重 + 视频
│   ├── kaggle_verify.ipynb   # Notebook 模板（T4/P100 + Internet + input dataset）
│   ├── README.md             # 推送/下载/人工核对流程
│   └── dataset/              # 本地组装区（.gitignore 忽略；视频/权重/产物不入库）
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
| POST | `/api/v1/tasks/{task_id}/stop` | 停止处理任务（协作式取消：置取消旗标，执行器在下一检查点——节点边界/ffmpeg 前/逐帧前/LLM 调用前——抛 `TaskCancelled` 走 failed 分支；任务须在本进程内存队列，终态/重启后任务返回 `stopped:false`） |
| POST | `/api/v1/tasks/{task_id}/retry` | 重试终态任务（失败/被停止）：按原 level + 输入视频 MD5 秒传重新提交，返回 `{retried_from, task_id, status}`；非终态任务 409、输入不可用 400、任务不存在 404；管线配置按提交时刻解析（与 `/api/v1/process` 一致） |
| GET | `/api/v1/tasks/{task_id}/report` | 下载 JSON 报告 |
| GET | `/api/v1/tasks/{task_id}/video` | 下载高光集锦视频 |
| GET | `/api/v1/tasks/{task_id}/artifact/{kind}` | 按 kind 下载任务产物文件（如 `track_overlay` 轨迹可视化诊断视频，来源 task_outputs 落库，缺失 404） |
| GET | `/api/v1/db/tasks` | 任务历史（数据库审计，`?limit=50`） |
| GET | `/api/v1/db/tasks/{task_id}` | 单任务完整结果（从数据库重建：highlight/report JSON + 文件路径，历史查看不依赖内存队列） |
| GET | `/api/v1/db/providers` | 模型服务商列表（数据库；字段 id/name/base_url/**api_key(掩码)**/models/default_model/enabled/sort_order/is_selected） |
| POST | `/api/v1/db/providers` | 新增服务商（body: name/base_url/api_key/models/enabled/sort_order；重复名 409、base_url 须 http(s)、models 至少 1 项） |
| PUT | `/api/v1/db/providers/{id}` | 编辑服务商（按 id 定位；api_key 留空表示保留原值；重复名 409） |
| DELETE | `/api/v1/db/providers/{id}` | 删除服务商（被 `ai.provider` 直选引用时返回 409） |
| POST | `/api/v1/db/providers/check-models` | 校验模型可用性：body 传 `base_url`/`api_key`/`models`，返回 list（GET /models）或逐模型 probe（chat/completions）结果 |
| GET | `/api/v1/config` | 配置 KV 列表（ai.* 服务商配置；**pipeline 类**：`llm.analysis_level`/`llm.highlight_strategy`/`pipeline.stages`（旧键 `highlight.level`/`llm.analysis_mode` 读回退兼容存量行）；select 项含动态选项、secret 掩码、source 标明 db/config/env/builtin） |
| PUT | `/api/v1/config/{key}` | 设置配置覆盖（如 `ai.provider` 切换服务商、`ai.model` 覆盖模型；**`pipeline.stages` 写入时校验 JSON 数组且元素 ∈ {preprocess,highlight,edit,report}，非法返回 400**；secret 空值=保留、等于默认值=自动删行） |
| DELETE | `/api/v1/config/{key}` | 删除配置覆盖（恢复默认值） |
| GET | `/api/v1/workflows` | 工作流预设列表（含 `is_active` 标记） |
| GET | `/api/v1/workflows/schema` | 节点目录 + 端口 + 参数 Schema（前端渲染依据） |
| POST | `/api/v1/workflows` | 新增工作流（图非法 400；重名 409） |
| GET | `/api/v1/workflows/{id}` | 工作流详情（含 graph JSON） |
| PUT | `/api/v1/workflows/{id}` | 更新工作流（内置 403；图非法 400） |
| DELETE | `/api/v1/workflows/{id}` | 删除工作流（内置/激活中 409） |
| POST | `/api/v1/workflows/{id}/activate` | 激活工作流（写入 `workflow.default_graph_id`） |
| POST | `/api/v1/workflows/{id}/clone` | 复制工作流为可编辑副本（内置也可复制，返回 `{id, name}`；名称自动「{原名} 副本 [序号]」避让，不存在 404） |
| POST | `/api/v1/workflows/validate` | 草稿校验（不落库），返回 `{ok, errors}` |

模型服务商以 OpenAI 兼容三要素（`base_url` / `api_key` / `models`）在 `backend/config.yaml` 的 `llm.providers` 声明（`models` 优先，缺省由 `model` 包装为单元素列表），通过 `llm.active_provider` 切换；该结构已落地到数据库 `ai_providers` 表（纯凭据目录：`id` 主键、`name` 唯一、`enabled` 用 int、无 `is_active`/`selected_model`）。`api_key` **直接入库明文**、列表/详情接口以掩码返回（**前 3 + 末 4 位**，如 `sk-****cdef`），管理页用密码框填写；每个服务商可配多个模型（`models` JSON 列表，首项为 `default_model`）。未配置 API Key 时 LLM 客户端自动回退 **Mock 模式**（`llm.mock_mode: auto`）。

- 激活服务商与选定模型不再存于 provider 行内，而是由 `system_config` 配置 KV 表覆盖：`ai.provider`（直选生效服务商，值可为某服务商名或 `custom`）、`ai.model`（覆盖所选服务商的默认模型，空=跟随默认）；另含 `ai.api_key`/`ai.base_url` 供 `custom` 独立配置。配置 KV 子系统由 `app/config_registry.py`（最小注册表）+ `app/services/config_service.py`（`get_ai_config`/`mask_secret`/配置覆盖）封装。
- 生效服务商的唯一事实来源为配置 KV `ai.provider`：运行时 LLM 调用（`app/utils/llm.py`）、`/health` 与启动自检（`app/utils/environment.py`）均经 `config_service.get_ai_config(config)` 解析——命中启用服务商则引用其 `api_key`/`base_url`、`model` 取 `ai.model` 覆盖或 `default_model`，否则回落静态 `config.active_provider`（DB 故障返回 `None` 由调用方降级，继续回落静态配置）。原 `db_service.get_active_provider()` 已移除。
- 管线全局策略同样走配置 KV 子系统（**不新增表、不需 Alembic 迁移**），经 `PUT /api/v1/config/{key}` 持久化（原「策略调整」模态框已随 d29dfae 移除，交互态策略主要由工作流节点参数承载；本节 KV 值作用于默认工作流图编译与旧管线路径），影响之后所有任务：
  - `llm.analysis_level`（分析层级：beginner/intermediate/professional/all，默认 intermediate）— 高光识别与报告的分析深度，`all`=所有高光回合（仅剪辑拼接、不生成技术分析报告）。
  - `llm.highlight_strategy`（高光识别媒体输入策略：frame=抽帧/video=视频理解，默认 frame）— 经 `app/utils/media_strategies.py` 的策略注册表分发；`video` 模式将整段 MP4 以 `data:video/mp4;base64,...` 内联为单个 `video_url` 块直送（置于文本之前），>128MB 仅 `logger.warning` 不切片，候选窗口作为软提示（模型自由定位），后处理仅做时长合法性 + 准备段过滤；`frame` 模式保持原抽帧行为零回归。`report` 固定 `analysis_mode="frame"` 不受影响。
  - `pipeline.stages`（启用的管线阶段有序 JSON 数组，默认 `["preprocess","highlight","edit","report"]`）— `config_service.set_config_value` 校验 JSON 数组且元素 ∈ 合法集合（非法 400），解析时按固定顺序重排。
  - 三项生效值统一由 `config_service.get_pipeline_config(db, config)` 解析（DB 覆盖 > 默认值），在 `main.process_video` 解析后透传 `core.run_pipeline(analysis_mode=, enabled_stages=)`。
- **可编排工作流系统**（`app/workflow/`）：用户可自定义 DAG 工作流，取代固定四阶段管线。
  - **节点契约**（`spec.py`）：`Port`（类型化端口）、`ParamSpec`（参数 Schema 驱动前端表单）、`NodeSpec`（完整节点规范）、`@register` 装饰器（导入即注册）。**声明式落库（批次 C2）**：`NodeSpec.persists=((输出端口, outputs.kind),...)` 声明产物文件落库、`NodeSpec.records_input=True` 声明输入元信息落库——由执行器在收尾阶段主线程统一调 `record_task_output`/`record_task_input`，节点不得直调 `db_service`（保持纯函数）。
  - **图结构**（`graph.py`）：`WorkflowGraph` 含节点列表 + 有向边 + 校验（R1~R10 十项规则）+ Kahn 拓扑排序；`frozen` 属性锁定预置图。
  - **执行器**（`executor.py`）：`Context` 传值 + stage 上报 + 产物投影（`output.artifact` 节点写回 `TaskResult`）。**分层并行（批次 C1）**：按 `graph.topo_levels()` Kahn 分层调度——准备（解析输入/级联判定/参数/节点级 config 深拷贝）与收尾（store/声明式落库/状态）在主线程按层内 node.id 升序执行（确定性）；同层节点按 `spec.stage` 分组，组间串行（`result.stage` 标量阶段上报顺序确定）、组内同阶段节点线程池并行（`detect.*` 同为 detecting 等），单节点组内联执行保持链式图串行语义；并行执行期无并发 DB 写（落库集中在收尾主线程）。
  - **预置编译器**（`presets.py`）：`compile_from_legacy(stages, strategy, level)` 将旧管线三元组编译为 `WorkflowGraph`；`compile_from_config(db, config)` 读取配置 KV 后委托编译。编译产出的图 `frozen=True`，禁止修改。
  - **16 个内置节点**：`input.video`（视频输入）、`preprocess.transcode`（预处理转码）、`detect.candidates`（信号候选定位）、`analyze.highlight`（LLM 高光识别）、`post.filter_segments`（片段过滤）、`post.uniform_slices`（均匀切片兜底）、`edit.concat`（剪辑合成）、`report.technical`（技术分析报告）、`output.artifact`（产物投影），以及 CV 感知层 6 节点（方案 12 · Step 2）：`detect.tracknet`（TrackNet 球追踪，track+candidates 双输出）、`detect.court`（球场 14 关键点）、`detect.player`（YOLOv8 球员运动分数→候选）、`detect.pose`（MediaPipe 姿态）、`post.score_highlights`（CLIP 零样本评分，脱离 LLM 产出 highlight）、`post.classify_strokes`（击球分类 cnn/dtw，pose 输入可选），以及 `post.visualize_track`（轨迹可视化诊断：球轨迹折线 + 球员框 + 检出时间线叠加视频，多球轨迹按 tracklet 分段配色、静止球灰标，产物 kind=`track_overlay`，on_failure=skip）。
  - **CV 感知层契约**：推理薄封装在 `app/utils/cv_*.py`（共享运行时 `cv_runtime.py`），候选聚合复用 `event_detect.track_to_candidates`/`scores_to_candidates`（与旧信号同一套聚类）；依赖/权重缺失抛 `CvUnavailable`，**4 个 detect 节点 `on_failure="skip"` 级联降级、2 个 post 节点保持 fail**；依赖组 `uv sync --extra cv`（torch/ultralytics/mediapipe/transformers），权重放 `backend/data/models/`（gitignore 忽略）；**一键获取转换**：`backend/scripts/fetch_tracknet_weights.py`（HF 源下载 → 契约适配 → TorchScript 导出落盘）；模块级导入零重依赖，无 CV 环境节点注册与 schema 照常工作。
  - **持久化**：`workflows` 表（name/graph_json/is_builtin/enabled），Alembic 迁移；`workflow_service.py` CRUD + 激活 + 复制（`clone_workflow`：内置也可复制为 `is_builtin=0` 可编辑副本，名称自动避让、graph_json 内嵌 name 同步）+ 种子。内置工作流（`is_builtin=1`）不可删除/修改，修改诉求经 `POST /{id}/clone` 走副本。**种子**（`seed_builtin_presets`，`main.py` 启动接线、失败仅告警不阻断）：「默认工作流」按全表 `count==0` 门控、「CV 增强工作流」（方案 12 · 5.2，示例 C 降本链路：TrackNet 候选 → CLIP 初筛 → 剪辑/报告，无 analyze 节点）与「CV 增强调试工作流」（示例 C + `post.visualize_track` 诊断分支 n2.video/n3.track/n2.duration → n8，产物 `track_overlay.mp4`；终端节点输出悬空合法、on_failure=skip 不影响主链路产物）按名幂等缺则补、存量库也生效。
- **任务重启恢复（僵尸任务修复）**：`main.py` 启动钩子（`init_db` 之后、环境自检之前）扫描 DB 非终态任务（pending/processing/timeout）——`db_service.recover_stale_tasks()` 从 `task_outputs`（kind=uploaded）反解输入 MD5，`uploaded_videos` 反查物理路径：文件仍在 → 保留原 task_id/level 经 `queue.submit` 重新入队续跑（预处理输出已落盘，仅重做 LLM/CV 推理）；文件缺失/未登记 → `mark_task_failed` 标记 failed 附中断原因。恢复块仅告警不阻断启动，单任务失败不影响其他任务（`tests/test_task_recovery.py` 覆盖 7 用例）。
  - **API**：`GET /api/v1/workflows/schema`（节点目录）、`GET/POST/PUT/DELETE /api/v1/workflows`（CRUD）、`POST /{id}/activate`、`POST /{id}/clone`（复制为可编辑副本）、`POST /validate`（草稿校验不落库）。
  - **运行时取值优先级**：显式 `workflow_id` > 激活工作流 > 由三 KV 编译的默认图。
  - **前端**：`WorkflowPanel.vue` 右侧滑出面板（预设列表 + 节点目录 + 参数表单 + JSON 导入导出），App.vue 头部「工作流」按钮。
- 管线阶段化（`app/core.py` 的 `run_pipeline`）：固定顺序 `[preprocess, highlight, edit, report]`，按 `enabled_stages` 集合启用/停用；依赖校验（`_validate_enabled_stages`）要求高光识别/剪辑/报告 依赖 预处理、剪辑/报告 依赖 高光识别（非法组合抛清晰 ValueError，任务判 FAILED），保证阶段编排安全。新增节点保持该契约。
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
- **可编排工作流**：新增节点类型须在 `app/workflow/nodes/` 下新建模块，用 `@register(NodeSpec(...))` 装饰器注册（`nodes/__init__.py` 自动发现导入，放文件即注册、无需改清单）；节点函数签名统一为 `run(ctx, params) -> dict[port, value]`；节点是 services 的薄封装，不重复业务逻辑。图校验规则 R1~R10 在 `graph.py` 的 `validate()` 中执行，不抛异常、返回结构化结果（含 R8 扩展：有高光生产节点时 `output.artifact` 缺 `highlight` 入边阻断）。**独立性契约（方案 12 · Step 0）**：执行器为每节点注入 `copy.deepcopy` 后的独立 `ctx.config`，节点内可直接改写、无须 try/finally 恢复；端口值一律函数式传递（`model_copy(update=...)`），禁止原地修改上游输出；端口值在 `store()`/`resolve_inputs()` 经 `spec.PORT_PY_TYPES` 运行时类型校验；`NodeSpec.on_failure`（默认 `fail`；`skip` = 级联锚点，节点失败后自身与下游级联标记 skipped、任务仍成功，CV/GPU 类节点应标 `skip`），上游正常输出 `None` 不触发级联；`optional=True` 节点失败仅标记 skipped、**不**记入死亡集合，下游按「来源存活、无产出」继续执行（如 report 失败不吞掉 `output.artifact` 已可投影的集锦/高光产物）。
- Prompt 模板集中在 `prompts/`（网球教学知识库注入点），勿散落在 service 内。
- 视频处理依赖系统 FFMPEG，新增调用走 `app/utils/ffmpeg.py` 封装。
- 日志统一走 `app/utils/logger.py`（基于 **loguru**），勿直接 `print`。
  - **获取 logger**：`from app.utils.logger import get_logger; logger = get_logger(__name__)`，返回已绑定模块名的 loguru `Logger`，现有 12 处调用点无需改动。
  - **统一格式规范**：`时间 | 级别 | 模块:函数:行号 - 消息`（时间毫秒精度 **UTC** 带偏移标志 `YYYY-MM-DD HH:mm:ss.SSS+00:00`，经 loguru `{time:...!UTC}` 强制换算、与服务器时区解耦，读取方按需转本地时间；级别按 `{level: <8}` 右补位）。
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
| `TENNISCLIP_LOG_ENQUEUE` | `1`（开） | loguru sink `enqueue` 开关；受限环境（Windows 沙箱/Kaggle dry-run，无 `multiprocessing` 管道权限）置 `0` 关 enqueue 改同步写，避免 `import` 崩溃 |

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
uv sync --extra cv           # 可选：CV 感知层依赖（torch/ultralytics/mediapipe/transformers）
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

# 失管后台服务排查/回收（根目录，对应「边界与注意事项」孤儿进程禁令）
python3 scripts/manage_services.py list             # 只读查询（--json 机器可读；退出码 0=干净/1=有目标）
python3 scripts/manage_services.py clean --dry-run  # 预览将清理的进程，不发信号
python3 scripts/manage_services.py clean --yes      # TERM→校验→KILL→复验（退出码 0=干净/1=有残留）
```

需系统安装 [FFMPEG](https://ffmpeg.org/)（Windows：`winget install Gyan.FFmpeg`）。

> 上述 `uvicorn` / `pnpm dev` / `docs:dev` 等常驻服务命令默认由**用户在自己的终端**启动；agent 不得自动以后台方式代启动（见「边界与注意事项」的孤儿进程禁令）。

## 边界与注意事项

- **禁止自动启动后台服务**：agent 不得以 `&` / `nohup` / `setsid` / `start / background` 等方式自行启动常驻服务（`uv run uvicorn`、`pnpm dev`、`pnpm run docs:dev`、nginx 等）并放任其在后台运行——会话结束后进程脱离管理、成为**孤儿进程**，用户难以发现与回收（端口占用、僵尸服务）。确需启动时：① 优先前台运行或使用带超时的短生命周期命令；② 必须先向用户说明用途与端口、获得同意；③ 用完立即在同一轮内停止并确认进程已退出（`kill` 后校验，勿只 `kill` 不确认）。
- **失管进程的查询与回收**用根目录 `scripts/manage_services.py`（纯标准库、只依赖 `/proc`）：`python3 scripts/manage_services.py list`（只读排查，含 `--json`，退出码 0=干净 / 1=有目标）、`... clean --dry-run`（预览不发信号）、`... clean --yes`（TERM → 等待校验 → 必要时 KILL → 复验端口，退出码 0=已清干净 / 1=有残留）。脚本自带安全过滤：永不清理自身进程链、IDE/code-server、grep/ps 等检索工具，且默认要求进程 cwd 在项目根内；勿另写一次性 `kill` 命令。
- `.gitignore` 已忽略：`backend/.venv/`、`backend/data/`（数据库/输入/输出/日志整体忽略）、`backend/data_test/`（测试数据目录，隔离于 data/）、`backend/.env.test`（测试配置，本地用不入库）、`backend/test_roundtrip.db`（根目录遗留测试库）、`frontend/node_modules/`、`frontend/dist/`、`__pycache__/`、`kaggle/dataset/`（Kaggle 验证包的本地组装区，视频/权重/产物不入库）。
- 提交时勿将生成数据库或视频文件加入版本控制。
- 本仓库已采用 MIT 协议（`LICENSE`），修改协议或版权署名需谨慎并同步 README。
- 验收指标：1–5 分钟视频端到端 ≤ 30s（不含模型推理网络延迟）；高光回合识别准确率 ≥ 90%（样本集离线评测）；连续 100 条批量无崩溃。
