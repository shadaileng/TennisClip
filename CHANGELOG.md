# Changelog

本文件记录 TennisClip AI 所有重要变更。

格式基于 Keep a Changelog，版本号遵循语义化版本。

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
