# Changelog

本文件记录 TennisClip AI 所有重要变更。

格式基于 Keep a Changelog，版本号遵循语义化版本。

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
