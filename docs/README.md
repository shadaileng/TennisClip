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
│   ├── 03-测试环境隔离方案.md
│   ├── 04-模型服务商管理.md
│   ├── 05-高光候选定位方案.md
│   ├── 06-两步上传与MD5秒传.md
│   ├── 07-分析分层挂钩高光.md
│   ├── 08-视频理解节点与策略模态框.md
│   ├── 09-可编排工作流方案.md
│   ├── 10-任务列表与历史记录界面.md
│   ├── 11-工作流模式整合主界面.md
│   └── 12-本地大模型部署可行性方案.md
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
| 04 | [模型服务商管理](plans/04-模型服务商管理.md) | v1.0.0 | 方案 | plans/ | 🏁 已完成 | 整合前端模型选择 + 每商多模型/直存密钥 + 对齐 TennisDiary 的配置 KV 驱动激活/选模型、ai_providers 重构、校验模型、id 路由、前端管理弹窗 |
| 05 | [高光候选定位方案](plans/05-高光候选定位方案.md) | v1.1.0 | 方案 | plans/ | 🚧 进行中 | 基于 ffmpeg 信号（场景切换+运动强度）定位高光候选窗口，LLM 段内精修时间戳，根治 LLM 时间戳凭空臆造 |
| 06 | [两步上传与 MD5 秒传](plans/06-两步上传与MD5秒传.md) | v1.0.0 | 方案 | plans/ | 🏁 已完成 | 视频两步上传：MD5 预检秒传（复用已落盘视频重新分析）+ 5MB 分片断点续传（crc32 + 整文件 MD5 二次校验），uploaded_videos 表去重 |
| 07 | [分析分层挂钩高光](plans/07-分析分层挂钩高光.md) | v1.0.0 | 方案 | plans/ | 📋 待执行 | 分析分层 level 驱动高光选择策略，新增「所有高光回合」档位：遍历整段、截取全部高光、不受 max_segments/target_duration 约束 |
| 08 | [视频理解节点与策略模态框](plans/08-视频理解节点与策略模态框.md) | v1.0.0 | 方案 | plans/ | 📋 待执行 | 新增视频理解策略节点（替换抽帧）+ 管线可配置（全局启用/停用阶段）+ 分析模式/层级全局配置 + 前端"策略调整"模态框 |
| 09 | [可编排工作流方案](plans/09-可编排工作流方案.md) | v2.0.0 | 方案 | plans/ | 🏁 已完成 | 固定四段管线升级为用户可编排 DAG 工作流：SVG 画布编辑器、节点增删/连线/启停、节点级参数、多套预设持久化与激活、简易模式兼容 |
| 10 | [任务列表与历史记录界面](plans/10-任务列表与历史记录界面.md) | v1.0.0 | 方案 | plans/ | 🚧 进行中 | 前端新增任务历史列表面板、任务详情弹窗（视频回放 + 分析报告 + 下载），支持查看所有历史处理记录 |
| 11 | [工作流模式整合主界面](plans/11-工作流模式整合主界面.md) | v1.2.0 | 方案 | plans/ | 🏁 已完成 | 去掉简易模式，主页面左右等宽两列，工作流用 WorkflowCanvas 全屏模态（ComfyUI SVG 节点画布）；TaskCard 步骤条动态化 |
| 12 | [本地大模型部署可行性方案](plans/12-本地大模型部署可行性方案.md) | v1.4.0 | 方案 | plans/ | 🚧 进行中 | Step 0 独立性加固 + Step 1 阶段1 Prompt 增强已实施；三阶段产物全部工作流节点化：阶段2 新增 6 个节点（4 detect + 2 post）+ 端口扩展 + 参数化 + 4 张组合示例图；含开源方案调研与硬件评估 |
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
| 04-模型服务商管理 | 🏁 已完成 | 整合 04/05/06：配置 KV 驱动激活/选模型 + ai_providers 重构 + check-models + id 路由 + 前端管理弹窗（TDD 先行） |
| 05-高光候选定位方案 | 🚧 进行中 | 代码与单测已完成（新增 event_detect + find_highlights 锚定 + 段内抽帧，全量 78 用例通过），待真实视频阈值标定与端到端验证 |
| 06-两步上传与 MD5 秒传 | 🏁 已完成 | 后端 upload_service + upload 路由（check/chunk/chunks/complete）+ process md5 引用；前端 spark-md5 增量计算与两步编排 + UploadPanel 状态 UI；测试 5 passed；commit 2f02662 |
| 07-分析分层挂钩高光 | 📋 待执行 | 待实施：level_strategies 映射 + resolve_selection、all 档位候选/段数不截断、HighlightResult.all_highlights、前端新增「所有高光回合」 |
| 08-视频理解节点与策略模态框 | 🏁 已完成 | 已实施：media_strategies 策略模块 + 管线阶段化依赖校验 + 全局配置三项 + 前端 StrategyModal |
| 09-可编排工作流方案 | 🏁 已完成 | 8 步全部完成：节点契约→9 节点实现→图校验/执行器→简易模式编译→持久化/API→SVG 画布编辑器→工作流执行集成→文档收尾（90+ 测试通过） |
| 10-任务列表与历史记录界面 | 🚧 进行中 | api.listTasks + store 历史状态 + TaskHistory 滑出面板 + TaskDetailModal 详情弹窗 + App.vue 集成已完成，待验证 |
| 11-工作流模式整合主界面 | 🏁 已完成 | Part A: 去掉简易模式，主页面左右等宽两列 + WorkflowCanvas 全屏模态；Part B: TaskCard 步骤条与工作流节点一一对应（后端上报 workflow_nodes） |
| 12-本地大模型部署可行性方案 | 🚧 进行中 | 已完成：Step 0 独立性加固（0.1~0.7）+ Step 1 阶段1 Prompt 增强（1.1~1.6：教学知识库 21 条/级分档 + tennis_domain 领域模块 + 视觉判别/few-shot/prompt_variant/knowledge_level 参数化，221 测试全绿）；待执行：1.7 评估数据集（需真实视频标注）、Step 2 阶段2 新增 6 节点（detect.tracknet/court/player/pose + post.score_highlights/classify_strokes）、Step 3 本地模型部署、Step 4 VLM 微调（批次 C 同层并行列为后续项） |

## 约定速查

- 编号：每个子目录独立从 01 递增
- 命名：`{NN}-{中文标题}.md`，无空格
- 版本：`v{major}.{minor}.{patch}`（重构+MAJOR / 增补+MINOR / 勘误+PATCH）
- 状态：📋待执行 / 🚧进行中 / 🏁已完成 / ⏳已归档
- 创建文档后**必须**同步侧边栏并运行校验脚本：
  `python .codebuddy/skills/docs-manage/scripts/sync_sidebar.py`
