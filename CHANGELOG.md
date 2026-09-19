# Changelog

本文件记录 TennisClip AI 所有重要变更。

格式基于 Keep a Changelog，版本号遵循语义化版本。

## [0.9.0] - 2026-09-19

### Added

- 分析分层 `level` 驱动高光选择策略：`beginner`/`intermediate`/`professional` 各对应不同高光数量档位（`max_segments` 2/3/5），由 `HighlightConfig.level_strategies` 映射 + `resolve_selection` 统一解析
- 新增「所有高光回合」档位 `level=all`：遍历整段视频、截取并拼接全部高光时刻，不受 `max_segments` 与 `target_duration` 约束；`HighlightResult.all_highlights` 作为全段拼接唯一事实来源，下游剪辑据此全段拼接，且 `core.run_pipeline` 在 all 档位跳过技术分析报告生成（仅剪辑、不分析）
- `event_detect.detect_candidates` 新增 `top_n` 参数（`None`=不截断），`build_highlight_prompt` 新增 `select_all` 全量返回约束，前端 `UploadPanel` 新增「所有高光回合」选项
- `POST /api/v1/process` 的 `level` 参数新增合法值校验（beginner/intermediate/professional/all，非法返回 400）

## [0.8.0] - 2026-09-18

### Added

- 视频上传实现「两步上传 + MD5 秒传」：前端增量计算文件 MD5 后先预检，命中则跳过上传、复用已落盘视频重新分析；未命中走 5MB 分片上传 + 断点续传（单片 crc32 校验、整文件 MD5/size 二次校验），按 `{md5}{ext}` 去重落盘
- 新增上传接口 `POST /api/v1/upload/check`（MD5 预检）、`POST /api/v1/upload/chunk`（分片）、`GET /api/v1/upload/chunks`（进度）、`POST /api/v1/upload/complete`（合并登记）
- `POST /api/v1/process` 新增可选 `md5` 字段：引用已上传视频实现秒传复用；保留原 `file` 整体直传兜底
- 新增 `uploaded_videos` 表（MD5 主键去重目录），配套 Alembic 迁移；前端 `UploadPanel` 展示「秒传命中 / 上传进度 / 分析中」状态

## [0.7.0] - 2026-09-17

### Added

- 模型服务商管理对齐 TennisDiary：激活服务商（`ai.provider`）与选定模型（`ai.model`）改由 `system_config` 配置 KV 覆盖，`get_ai_config` 统一解析；`model_providers` 重构为 `ai_providers`（id 主键、enabled 用 int、去除 is_active/selected_model）
- 新增 `check-models` 模型可用性校验接口（GET /models 清单优先，否则逐模型 chat/completions 探测），前端弹窗逐模型显示 ✓/✗
- 服务商增删改路由改按整数 id；删除被 `ai.provider` 直选引用的服务商返回 409；API Key 掩码改为前 3 + 末 4 位
- 新增 `/api/v1/config` 配置端点（列表/覆盖/恢复），`/health`、LLM 调用、启动自检均经配置解析，DB 不可用时回落静态配置
- 前端服务商管理弹窗（表格 + 内联多模型表单 + 逐模型校验）与上传面板配置直选下拉

## [0.6.0] - 2026-09-16

### Added

- 根目录搭建 VitePress 文档站：新增根 `package.json` 与 `vite.config.js`，文档内容根指向 `docs/`；`docs:dev`/`docs:build`/`docs:preview` 脚本可用；`docs/.vitepress/config.mts` 配置 `host`/`allowedHosts` 与 `ignoreDeadLinks`，新增 `references/`、`guides/` 分区首页
- 任务异步化重构：上传文件后立即创建任务并返回 `task_id`；任务交由后台 `TaskQueue` 线程池异步执行管线（预处理 → 高光识别 → 剪辑合成 → 技术分析报告），逐阶段落库
- 前端按 `task_id` 轮询任务状态；任务异常（如 FFMPEG 不可用）即终止并回填 `error` 字段，前端展示具体错误而非持续 500 轮询

## [0.5.1] - 2026-09-16

### Fixed

- 修复日志端到端测试（TC-04）在完整测试套件下偶发失败：第三方库（uvicorn 等）在导入期可能将 `uvicorn.access` 等 logger 的 `disabled` 置为 `True` 或调用 `logging.disable(...)`，导致标准 logging 被静默禁用、`InterceptHandler` 收不到日志。`app/utils/logger.py` 的 `_intercept_stdlib_logging` 接管时复位 `logging.disable(0)` 及被接管 logger 的 `level`/`disabled`；`tests/test_logging.py` 的 `_reset_logging` 夹具新增 `_reset_stdlib`，在每个用例前后重置标准库 logging 全局状态，实现真正隔离

## [0.5.0] - 2026-09-16

### Added

- 启动环境自检：服务启动时对 FFMPEG 安装、数据库连接、模型提供商配置、数据目录可写做四项检查，失败仅告警、不阻断启动
- `/health` 新增 `environment` 字段，返回启动自检明细（ffmpeg / database / provider / data_dir，status 为 ok/warn/fail）
- 收口 `/health` 契约：原顶层 `ffmpeg` / `database` 字段已并入 `environment`（开发阶段不做向后兼容）；`provider` 改为嵌套对象 { name / model / base_url / api_key_set / source }，`source` 标识生效来源（`database` 或 `config`）
- 生效提供商统一以数据库 `model_providers` 的 `is_active` 记录为准：运行时 LLM 调用、`/health`、启动自检均优先读 DB 生效记录（`db_service.get_active_provider()`），使 `/api/v1/db/providers/{name}/activate` 的切换真正生效；数据库不可用时回退静态 `config.active_provider`

### Changed

- 模型提供商种子写入改用 `init_db` 已持有的 `config`：去掉 `db_models._seed_providers` 内部重复的 `load_config()`，避免多重配置来源下 `is_active` 判定以磁盘 `config.yaml` 为准而漂移；`db.py` 的 `init_db(engine)` 路径仍就地 `load_config()` 后传入

## [0.4.1] - 2026-09-16

### Changed

- 重构测试环境隔离：`load_config()` 不再硬编码测试分支与 `data_test` 兜底，改为通用 `.env.<env>` 约定（`TENNISCLIP_ENV=test` → `.env.test`）；测试数据目录兜底（未声明 `TENNISCLIP_DATA_DIR` 时默认 `data_test`）移至 `backend/tests/conftest.py` 初始化阶段，公共代码保持环境无关

## [0.4.0] - 2026-09-15

### Added

- 引入 `.env.test` 测试配置隔离：设置 `TENNISCLIP_ENV=test` 后 `load_config()` 自动加载 `backend/.env.test`，与开发/生产配置彻底隔离
- 测试数据统一落入 `backend/data_test/`（由 `.env.test` 的 `TENNISCLIP_DATA_DIR=data_test` 驱动），绝不触碰真实 `backend/data/`
- 新增 `backend/tests/conftest.py`：pytest 启动时设置 `TENNISCLIP_ENV=test` 并预建 `data_test/` 目录
- 仅提交 `backend/.env.test.example` 模板，`.env.test` 不入库（`.gitignore` 忽略 `backend/.env.test` 与 `backend/data_test/`）
- 新增文档 `docs/plans/03-测试环境隔离方案.md`

## [0.3.0] - 2026-09-15

### Added

- 新增环境变量 `TENNISCLIP_DATA_DIR`：覆盖数据目录（`paths.data_dir`），优先级最高，可整体迁移数据库/输入/输出/日志

## [0.2.0] - 2026-09-15

### Added

- 后端接入 loguru 日志系统：统一 `时间 | 级别 | 模块:函数:行号 - 消息` 格式，控制台（彩色）+ 滚动文件（`data/app.log`，rotation=10MB / retention=7d / compression=zip）
- 接管 uvicorn / FastAPI 标准 logging（InterceptHandler），全链路同一套日志格式
- 日志级别来源：`TENNISCLIP_LOG_LEVEL` 环境变量 > `config.yaml` 的 `logging.level` > 默认 `INFO`
- 新增 `backend/app/utils/logger.py`（基于 loguru），27 处调用点迁移至 `get_logger(__name__)` + `{}` 延迟求值占位符

### Changed

- 目录布局调整：输入（sample_videos）、输出（outputs）、日志（app.log）、数据库（tennisclip.db）统一归入 `backend/data/`（`data_dir`），`.gitignore` 简化为整体忽略 `backend/data/`

## [0.1.5] - 2026-07-08

### Added

- 后端数据迁移改用 Alembic：`init_db` 服务启动时自动升级，旧库 `create_all` 兜底并 `stamp head`
- 新增 `backend/alembic/` 迁移目录（`env.py` 动态读 `DATABASE_URL`/`config.yaml`）与初始迁移 revision（6 张表）
- 文档同步更新 Alembic 迁移说明（README / AGENTS / `.env.example`）

### Changed

- `app/db.py` 与 `app/services/db_service.py` 的建表逻辑由裸 `create_all` 改为「Alembic 优先 + create_all 兜底」

## [0.1.4] - 2026-07-08

### Fixed

- 修复 VideoPlayer.vue 重复 `</script>` 标签导致 Vite 编译报错「Invalid end tag」

## [0.1.3] - 2026-07-08

### Added

- 部署模式：开发环境 Vite dev server 代理后台，生产环境前后端分开部署
- 前端 API 客户端支持 `VITE_API_BASE_URL` 构建注入后端基地址（跨域部署）
- 后端 FastAPI 新增可配置 CORS 中间件（`cors.allowed_origins`，默认 `*`，可被 `CORS_ALLOWED_ORIGINS` 覆盖）
- 文档同步更新部署模式与前后端环境变量说明（README / AGENTS）

## [0.1.2] - 2026-07-08

### Added

- 新增根目录 `AGENTS.md`，为 AI 编码代理提供项目上下文、编码约定与边界

## [0.1.1] - 2026-07-08

### Added

- 新增 MIT 开源协议（`LICENSE`），并在 README 中说明许可信息

## [0.1.0] - 2026-07-08

### Added

- 初始化 TennisClip AI 前后端分离项目结构与文档体系
- 新增后端 FastAPI 服务、命令行入口、数据库模型与测试
- 新增前端 Vue 3 / Vite / Pinia / Tailwind 页面与组件
- 集成 docs-manage 与 git-commit 项目 skills
- 添加 StepFun / OpenAI 兼容 provider 配置与 mock 回退能力
- 提供 SQLite / PostgreSQL / MySQL 多数据库适配与自动落库
- 增加高光识别、自动剪辑、结构化动作技术报告生成流水线
