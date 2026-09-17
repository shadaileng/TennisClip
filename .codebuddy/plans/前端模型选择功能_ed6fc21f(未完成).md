---
name: 前端模型选择功能
overview: 在前端 UploadPanel 中新增模型提供商选择控件，调用后端 `/api/v1/db/providers` 拉取列表、`/api/v1/db/providers/{name}/activate` 运行时切换，使 /health 与后续处理实际使用所选模型。
design:
  architecture:
    framework: vue
  styleKeywords:
    - Dark Mode
    - Glassmorphism
    - Emerald Accent
    - Minimalist
  fontSystem:
    fontFamily: PingFang SC
    heading:
      size: 16px
      weight: 600
    subheading:
      size: 14px
      weight: 500
    body:
      size: 13px
      weight: 400
  colorSystem:
    primary:
      - "#10b981"
      - "#34d399"
    background:
      - "#020617"
---

## 用户需求
前端缺少模型（提供商）选择功能，需要在网页界面上让用户查看可选的模型提供商，并切换当前生效的模型。

## 产品概述
在现有前端网球视频处理界面中新增「模型选择」能力。后端已提供 `GET /api/v1/db/providers`（列出所有提供商及激活状态）与 `POST /api/v1/db/providers/{name}/activate`（运行时切换生效提供商）。前端需拉取列表、展示可选项、支持用户切换，并即时反映到 `/health` 与后续视频处理所使用模型。

## 核心功能
- 列表展示：调用后端接口获取全部模型提供商（名称、模型、激活状态），渲染为可选项。
- 模型切换：用户选择后调用 `activate` 接口进行运行时切换，切换成功后同步当前生效模型。
- 状态反馈：展示加载中、切换中、加载失败/无可用模型等边界态，失败不阻断上传流程。
- 即时同步：切换后同步 `health.provider`，使顶部状态栏即时显示新模型。


## 技术栈选择
- 前端：Vue 3 + Vite + Pinia + Tailwind CSS（与现有项目完全一致，无新增依赖）
- 后端：复用既有 FastAPI 接口，无需改动后端代码

## 实现方案
- 采用「列表预拉取 + 下拉选择 + 即时激活」策略：组件挂载时通过 `api.js` 调 `GET /api/v1/db/providers` 拉取列表，用户在下拉框选择后用 `POST /api/v1/db/providers/{name}/activate` 切换；因 `/process` 不接收 provider 参数、直接使用数据库 `is_active` 记录，故切换在提交前完成，与后端契约一致。
- 关键决策：
  1. 状态落 Pinia store（`stores/task.js`）而非组件局部 state，便于 `UploadPanel` 与 `HealthBar` 共享当前生效模型。
  2. 用 `<select>` 下拉而非按钮组：兼容提供商数量可能较多的情况，且与现有 level 按钮组风格统一（深色 slate + emerald 高亮）。
  3. 切换成功后将 `health.provider` 更新为所选项，使 `HealthBar` 即时反映，无需额外轮询 `/health`。
- 性能与可靠性：列表仅挂载时拉取一次；切换为单次请求；加载/切换失败捕获异常并降级（禁用控件或提示），不阻断上传主流程，避免无限刷屏。

## 实现要点
- 复用 `lib/api.js` 既有 `url()`/`parse()`/`ApiError`，新增 `listProviders()`（解析列表）与 `activateProvider(name)`（解析 `{ok, active_provider}`）。
- `stores/task.js` 新增状态 `providers`、`activeProvider`（当前 is_active 的 name）、`loadingProviders`、`providerError`；动作 `loadProviders()`（清空/容错后填充并定位 active）、`activateProvider(name)`（调接口、刷新列表、回写 `health.provider`）。
- `UploadPanel.vue` 在「分析层级」块下方新增「模型选择」块：`<select>` 展示 `name — model`，`is_active` 项默认选中；`@change` 调 `activateProvider`，显示切换中禁用态与错误提示。
- `App.vue` 在 `onMounted` 与 `checkHealth()` 并列调用 `store.loadProviders()`，保证刷新后即有列表。
- `ApiError` 捕获 404（提供商不存在）与网络错误，给出中文提示。

## 架构设计
- 数据流：App 挂载 → `loadProviders()` → `api.listProviders()` → store.providers/activeProvider → UploadPanel 渲染下拉。
- 用户切换 → `activateProvider(name)` → `api.activateProvider(name)` → 成功刷新列表并同步 `health.provider` → HealthBar 即时显示。
- 提交视频时沿用已激活提供商，无需额外参数。

## 目录结构
```
frontend/src/
├── lib/
│   └── api.js                  # [MODIFY] 新增 listProviders() 与 activateProvider(name)，复用 url()/parse()/ApiError
├── stores/
│   └── task.js                 # [MODIFY] 新增 providers/activeProvider/loadingProviders/providerError 状态，
│                               #           loadProviders()、activateProvider(name) 动作，成功后回写 health.provider
├── components/
│   └── UploadPanel.vue         # [MODIFY] 在「分析层级」下方新增「模型选择」下拉块，onMounted 拉取，切换调 activate
└── App.vue                     # [MODIFY] onMounted 中调用 store.loadProviders()（与 checkHealth 并列）
```



## 设计风格
沿用现有深色科技风（slate-950 渐变背景 + emerald 强调色 + 玻璃拟态圆角卡片）。新增「模型选择」块与「分析层级」块视觉一致：圆角 2xl 卡片内 label 在左、控件在右，下拉框采用深色 slate-800 背景、emerald-400 高亮激活项。切换中下拉禁用并降低透明度，错误时下方出现 red-300 小字提示。整体响应式、与上传主流程互不干扰。

## 页面规划（UploadPanel 内新增块）
- 模型选择块（位于分析层级下方）：左侧「模型」文字标签，右侧 `<select>` 下拉，选项展示「名称 — 模型」，当前 is_active 选中；右侧可附一个小型加载/状态点。
- 状态反馈：加载中显示「加载模型中…」；切换中禁用下拉；失败显示提示并允许重试，不阻断上传。

