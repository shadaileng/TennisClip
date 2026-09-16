# 架构总览：任务处理流程

> **本页信息**
>
> | 项目 | 内容 |
> |------|------|
> | 文档编号 | 00 |
> | 文档版本 | v1.0.0 |
> | 文档状态 | 🚧 进行中 |
> | 最后更新 | 2026-09-16 |
> | 对应功能/内容 | 上传 → 创建任务 → 后台异步执行 → 轮询 → 异常终态的错误传播机制 |
>
> **变更历史**
>
> | 日期 | 版本 | 说明 |
> |------|:----:|------|
> | 2026-09-16 | v1.0.0 | 初版：梳理任务处理全链路与异常终态设计 |
>
> **关联文档**：[需求分析与落地方案](../../plans/01-需求分析与落地方案.md)、[后端 loguru 日志 TDD 方案](../../plans/02-后端loguru日志TDD方案.md)

## 一、背景与目标

前端上传视频后，后端需在不阻塞 HTTP 响应的前提下完成「预处理 → 高光识别 → 自动剪辑 → 报告生成」全链路，并让前端能通过轮询实时感知进度与结果。

本页定义任务处理的标准流程与契约，重点明确：

1. **上传即创建任务并返回 `task_id`**（身份一致，避免查询时任务不存在）。
2. **后台异步执行**，请求立即返回，处理在后台线程池进行。
3. **前端轮询** `GET /api/v1/tasks/{task_id}` 获取状态。
4. **任务异常即终态**：状态置为 `failed`（或 `timeout`），并写入 `error` 异常原因，前端停止轮询并展示。

> 设计动因：早期实现存在 `task_id` 错位（`main.py` 与 `TaskQueue.submit` 各自生成 id）+ `_wrapped` 无条件覆盖为 `succeeded` 两个问题，导致管线已失败但前端永远轮询收到 500、界面卡在「处理中」。本流程即修复后的目标形态。

## 二、流程总览

```text
========== 前端 ==========
用户上传视频
   │
   ▼
POST /api/v1/process
   │
   ▼
收到 {task_id, status: pending}
   │
   ▼
启动轮询 setInterval(poll, 1.5s)
   │
   ▼
┌── 轮询 GET /tasks/{id} ──────────────┐
│ status=pending/processing → 回到轮询  │
│ status=succeeded        → 展示集锦+报告│
│ status=failed/timeout   → 展示 error 原因，停止轮询
│ HTTP 404                → 停止轮询，提示任务不存在
│ 连续 5xx 超阈值         → 停止轮询，置 error
└──────────────────────────────────────┘

========== 后端 API ==========
POST /process
   │
   ▼
写文件到 uploads/
   │
   ▼
生成 task_id
   │
   ▼
db.record_task_output(上传记录)
   │
   ▼
queue.submit(job, task_id)   ← 注册 TaskResult(PENDING)
   │
   ▼
返回 {task_id, status: pending}

========== 后台异步执行（TaskQueue 线程池） ==========
_wrapped: status = processing
   │
   ▼
job() → run_pipeline
   │
   ▼
┌── 执行结果 ───────────────────────────────┐
│ 正常返回 TaskResult     → 用 out 替换存储结果，status=succeeded
│ 内部异常被 core 捕获    → out.status=failed，out.error=原因
│ job 抛未预期异常        → status=failed，error=str(exc)
│ 超时                    → status=timeout
└───────────────────────────────────────────┘
   │
   ▼
record_task_finish 落库终态

========== 调度关系 ==========
queue.submit ──线程池调度──▶ _wrapped
前端轮询 ──GET /tasks/{id}──▶ get_task 返回真实结果（status / error）

========== 异常示例 ==========
FFMPEG 不可用 → video_editor 抛 RuntimeError
   → out.status=failed
   → error = "FFMPEG 不可用，无法渲染集锦。请安装 ffmpeg 后重试。"
```

## 三、各环节实现要点

### 1. 上传即创建任务返回 id（`backend/app/main.py`）

- 接收文件后写入 `uploads/`，生成 `task_id`。
- 调用 `queue.submit(job, task_id=task_id)`，**将同一 `task_id` 传入队列**，先在队列注册 `TaskResult(PENDING)`。
- 接口立即返回 `{"task_id": task_id, "status": "pending"}`，不等待处理完成。

### 2. 后台异步执行（`backend/app/utils/tasks.py`）

- `TaskQueue` 基于 `ThreadPoolExecutor`（`config.queue.max_concurrent_tasks` 限流），后台线程执行 `job()`。
- `_wrapped` 工作流程：
  - 进入即置 `status = processing`；
  - `job()` 返回 `TaskResult` 时，**用真实结果对象替换队列中存储的结果**（携带真实 `status` / `error` / `highlight` / `report`），不再无条件覆盖为 `succeeded`；
  - 捕获 `Exception` → `status = failed`、`error = str(exc)`；捕获 `TimeoutError` → `timeout`；
  - 终态经 `db_service.record_task_finish` 同步落库。

### 3. 前端轮询（`frontend/src/stores/task.js`）

- 提交成功后 `setInterval` 每 1.5s 轮询 `api.getTask(task_id)`。
- 收到终态（`succeeded` / `failed` / `timeout`）→ 停止轮询、`loading=false`。
- 健壮性：遇到 `404` 立即停止并提示「任务不存在或已过期」；遇到连续 `5xx` 超过阈值（如 5 次）停止轮询、置 `error` 并提示，避免无限刷新。

### 4. 异常即终态并填写原因

- 管线在 `app/core.py` 的 `run_pipeline` 内以 `try/except` 兜底：任意节点异常（如剪辑节点 FFMPEG 不可用抛 `RuntimeError`）均被捕获，置 `result.status = "failed"`、`result.error = str(exc)`。
- `_process_one` 返回该 `TaskResult`，`_wrapped` 将其替换为队列存储结果，错误原因随 `GET /tasks/{id}` 的 `error` 字段返回前端。
- 前端 `TaskCard.vue` 在 `failed` / `timeout` 时渲染 `处理失败：&#123;&#123; task.error &#125;&#125;`。

## 四、API 契约（任务相关）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/process` | 上传视频，写文件并创建任务，返回 `task_id`、状态 `pending` |
| GET | `/api/v1/tasks/{task_id}` | 查询任务状态与结果；任务不存在返回 `404` |
| GET | `/api/v1/tasks/{task_id}/report` | 下载 JSON 报告（未就绪返回 `404`） |
| GET | `/api/v1/tasks/{task_id}/video` | 下载高光集锦（未就绪返回 `404`） |

`TaskResult` 关键字段：`task_id`、`status`（`pending`/`processing`/`succeeded`/`failed`/`timeout`）、`error`、`highlight`、`report`、`highlight_video_path`、`report_path`、`elapsed_seconds`。

## 五、环境依赖与失败兜底

- 视频处理依赖系统 **FFMPEG**。FFMPEG 不可用时剪辑节点抛错，任务以 `failed` 终态返回原因「FFMPEG 不可用，无法渲染集锦。请安装 ffmpeg 后重试。」——前端正常展示，不会卡死。
- 启动期 `/health` 的 `environment.ffmpeg` 项（ok/fail）可前置提示；建议在上传前前端读取 `/health` 并在 FFMPEG 不可用时直接拦截并提示安装。

## 六、关联文档

- [需求分析与落地方案](../../plans/01-需求分析与落地方案.md)
- [后端 loguru 日志 TDD 方案](../../plans/02-后端loguru日志TDD方案.md)
