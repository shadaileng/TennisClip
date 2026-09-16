"""FastAPI 服务入口（Web API + 托管前端静态构建 + 数据库管理端点）。

启动：uvicorn app.main:app --host 0.0.0.0 --port 8000
前端构建（frontend/dist）存在时，直接由本服务托管，单端口部署。
数据库多兼容（SQLite 默认，可切 Postgres/MySQL），记录任务/输入/输出/结果快照/提供商/文件。
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import load_config
from app.models import TaskResult, TaskStatus
from app.utils.logger import get_logger, setup_logging
from app.utils.tasks import TaskQueue
from app.core import run_pipeline
from app.services import db_service
from app.db_models import ModelProvider

# 尽早接管日志（含 uvicorn 内置日志），保证启动期日志统一格式输出
setup_logging()
logger = get_logger(__name__)

config = load_config()
app = FastAPI(title="TennisClip AI", version="0.1.0")

# CORS（前后端分开部署时放行跨域；同源部署下无副作用）
# allowed_origins 来自 config.cors（config.yaml 的 cors.allowed_origins，
# 或被环境变量 CORS_ALLOWED_ORIGINS 逗号分隔覆盖）。含 "*" 时 FastAPI 自动处理。
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors.allowed_origins,
    allow_credentials=False,  # 放行 * 时不能同时允许携带凭证
    allow_methods=["*"],
    allow_headers=["*"],
)

queue = TaskQueue(config)
_upload_dir = config.output_path / "uploads"
_upload_dir.mkdir(parents=True, exist_ok=True)

# 初始化数据库（多兼容：SQLite/Postgres/MySQL，按 config.database.url）
db_service.init_db(config)

# 启动环境自检（FFMPEG / 数据库 / 模型提供商 / 数据目录）
# 任一检查不通过仅告警、不阻断启动；结果挂载到 app.state 供 /health 暴露
from app.utils.environment import run_startup_checks

app.state.environment_checks = run_startup_checks(config)

# 前端静态资源（frontend/dist 存在时由 FastAPI 直接托管，单端口部署）
_BACKEND_ROOT = Path(__file__).resolve().parent.parent        # .../backend
_FRONTEND_DIST = _BACKEND_ROOT.parent / "frontend" / "dist"  # .../TennisClip/frontend/dist
_web_available = _FRONTEND_DIST.is_dir()


@app.on_event("shutdown")
def _shutdown() -> None:
    queue.shutdown(wait=True)


@app.get("/health")
def health() -> dict:
    """健康检查：provider 块反映数据库生效记录（is_active），DB 不可用时回退静态配置。"""
    active = db_service.get_active_provider()
    if active is not None:
        provider = {
            "name": active.name,
            "model": active.model,
            "base_url": active.base_url,
            "api_key_set": bool(os.environ.get(active.api_key_env, "")),
            "source": "database",
        }
    else:
        try:
            p = config.active_provider
            provider = {
                "name": p.name,
                "model": p.model,
                "base_url": p.base_url,
                "api_key_set": bool(config.api_key),
                "source": "config",
            }
        except ValueError as exc:
            logger.warning("health: 无法解析静态 provider：{}", exc)
            provider = {
                "name": None, "model": None,
                "base_url": None, "api_key_set": False, "source": "config",
            }

    return {
        "status": "ok",
        "provider": provider,
        "environment": {
            name: result.to_dict()
            for name, result in app.state.environment_checks.items()
        },
    }


def _process_one(video_path: Path, task_id: str, level: str = "intermediate") -> TaskResult:
    started = time.monotonic()
    result = TaskResult(task_id=task_id, source_video=str(video_path))
    try:
        run_pipeline(video_path, config, result, level=level)
    finally:
        result.elapsed_seconds = time.monotonic() - started
    return result


@app.post("/api/v1/process")
async def process_video(file: UploadFile, level: str = "intermediate") -> dict:
    """上传视频，异步提交处理任务。"""
    task_id = uuid.uuid4().hex[:12]
    suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
    dest = _upload_dir / f"{task_id}{suffix}"
    data = await file.read()
    dest.write_bytes(data)
    # 落库：上传文件记录
    db_service.record_task_output(task_id, "uploaded", str(dest), len(data) / (1024 * 1024))

    queue.submit(lambda: _process_one(dest, task_id, level))
    return {"task_id": task_id, "status": TaskStatus.PENDING.value}


@app.get("/api/v1/tasks/{task_id}")
def get_task(task_id: str) -> dict:
    result = queue.get(task_id)  # KeyError → 404
    return result.model_dump()


@app.get("/api/v1/tasks/{task_id}/report")
def get_report(task_id: str) -> FileResponse:
    result = queue.get(task_id)
    if not result.report_path:
        raise HTTPException(404, "report not ready")
    return FileResponse(result.report_path, filename=result.report_path.split("/")[-1])


@app.get("/api/v1/tasks/{task_id}/video")
def get_video(task_id: str) -> FileResponse:
    result = queue.get(task_id)
    if not result.highlight_video_path:
        raise HTTPException(404, "video not ready")
    return FileResponse(result.highlight_video_path, filename=result.highlight_video_path.split("/")[-1])


# ---------- 数据库管理端点（多兼容：SQLite/Postgres/MySQL） ----------

@app.get("/api/v1/db/tasks")
def db_task_history(limit: int = 50) -> list[dict]:
    """最近任务记录（审计/管理用），从数据库读取。"""
    tasks = db_service.get_task_history(limit)
    return [
        {
            "task_id": t.task_id,
            "status": t.status,
            "level": t.level,
            "source_video": t.source_video,
            "error": t.error,
            "elapsed_seconds": t.elapsed_seconds,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in tasks
    ]


@app.get("/api/v1/db/providers")
def db_providers() -> list[dict]:
    """模型提供商列表（数据库动态配置）。"""
    return [
        {
            "name": p.name,
            "base_url": p.base_url,
            "api_key_env": p.api_key_env,
            "model": p.model,
            "is_active": p.is_active,
        }
        for p in db_service.list_providers()
    ]


@app.post("/api/v1/db/providers/{name}/activate")
def activate_provider(name: str) -> dict:
    """切换当前生效的模型提供商（数据库动态切换）。"""
    ok = db_service.activate_provider(name)
    if not ok:
        raise HTTPException(404, f"provider '{name}' not found in database")
    return {"ok": True, "active_provider": name}


# ---------- 前端静态资源托管 ----------
if _web_available:
    # 静态资源（assets 等）
    _assets_dir = _FRONTEND_DIST / "assets"
    if _assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(_FRONTEND_DIST / "index.html")

    # SPA fallback：所有未匹配的非 /api 路由返回 index.html
    @app.exception_handler(404)
    async def spa_404(request: Request, exc: Exception):
        if request.url.path.startswith("/api") or request.url.path.startswith("/health"):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        index_file = _FRONTEND_DIST / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return JSONResponse(
            status_code=404,
            content={"detail": "前端未构建，请先在 frontend/ 下执行 pnpm build"},
        )
