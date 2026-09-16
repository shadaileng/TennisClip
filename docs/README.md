# 文档中心

本目录是 TennisClip AI 项目的统一文档管理区，遵循 [docs-manage skill](../.codebuddy/skills/docs-manage/SKILL.md) 约定。

## 目录结构

```
docs/
├── README.md                 # 本文件：文档索引与执行总览
├── _template.md              # 文档创建模板
├── index.md                  # VitePress 首页
├── .vitepress/config.mts     # VitePress 站点配置（侧边栏必须同步）
├── plans/                    # 方案类（📋待执行 → 🚧进行中 → 🏁已完成）
│   ├── 01-需求分析与落地方案.md
│   ├── 02-后端loguru日志TDD方案.md
│   └── 03-测试环境隔离方案.md
├── architecture/             # 架构类（持续维护）
│   └── index.md              # 架构总览（含任务处理流程）
├── references/               # 参考类（随代码更新）
├── guides/                   # 指南/手册类（持续维护）
└── reference/                # 参考代码（.gitignore，不入库）
```

## 文档一览

| 编号 | 文档 | 版本 | 类型 | 位置 | 状态 | 说明 |
|:----:|------|:----:|:----:|:----:|:----:|------|
| 01 | [需求分析与落地方案](plans/01-需求分析与落地方案.md) | v1.0.0 | 方案 | plans/ | 🚧 进行中 | 完整需求分析、技术选型、全链路、风险管控、验收标准、迭代规划 |
| 02 | [后端 loguru 日志 TDD 方案](plans/02-后端loguru日志TDD方案.md) | v1.0.0 | 方案 | plans/ | 📋 待执行 | 后端接入 loguru：双 sink、统一格式、uvicorn 拦截、TDD 测试用例 |
| 03 | [测试环境隔离方案](plans/03-测试环境隔离方案.md) | v1.0.0 | 方案 | plans/ | 📋 待执行 | 引入 `.env.test` 与 `data_test` 隔离测试配置/数据，仅提交 `.env.test.example` |
| 00 | [架构总览（任务处理流程）](architecture/index.md) | v1.0.0 | 架构 | architecture/ | 🚧 进行中 | 上传→创建任务→后台异步→轮询→异常终态的错误传播机制 |

## 文档类型说明

| 类型 | 生命周期 | 适用 |
|:----:|---------|------|
| 方案 | 📋待执行 → 🚧进行中 → 🏁已完成 | 功能实施前的设计 |
| 架构 | 持续维护 | 系统整体技术说明 |
| 参考 | 随代码更新 | API、数据字典 |
| 指南 | 持续维护 | 开发/使用流程 |

## 执行进度

| 文档 | 当前进度 | 说明 |
|------|---------|------|
| 01-需求分析与落地方案 | 后端脚手架 + 前端界面已完成 | 后端 Mock 全链路跑通，前端 Vue3 完整；待接入真实 Step 3.7 Flash API |
| 02-后端 loguru 日志 TDD 方案 | 🏁 已完成 | 测试先行（TC-01~TC-06），再实现 logger.py 双 sink 与 uvicorn 拦截 |
| 03-测试环境隔离方案 | 📋 待执行 | 引入 `.env.test` + `data_test`，测试配置/数据与开发/生产彻底隔离 |

## 约定速查

- 编号：每个子目录独立从 01 递增
- 命名：`{NN}-{中文标题}.md`，无空格
- 版本：`v{major}.{minor}.{patch}`（重构+MAJOR / 增补+MINOR / 勘误+PATCH）
- 状态：📋待执行 / 🚧进行中 / 🏁已完成 / ⏳已归档
- 创建文档后**必须**同步侧边栏并运行校验脚本：
  `python .codebuddy/skills/docs-manage/scripts/sync_sidebar.py`
