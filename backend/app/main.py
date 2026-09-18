"""FastAPI 服务入口（Web API + 托管前端静态构建 + 数据库管理端点）。

启动：uvicorn app.main:app --host 0.0.0.0 --port 8000
前端构建（frontend/dist）存在时，直接由本服务托管，单端口部署。
数据库多兼容（SQLite 默认，可切 Postgres/MySQL），记录任务/输入/输出/结果快照/提供商/文件。
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import load_config
from app.models import TaskResult, TaskStatus
from app.utils.logger import get_logger, setup_logging
from app.utils.tasks import TaskQueue
from app.core import run_pipeline
from app.routers import upload as upload_router
from app.services import db_service, upload_service

# 尽早接管日志（含 uvicorn 内置日志），保证启动期日志统一格式输出
setup_logging()
logger = get_logger(__name__)

config = load_config()
app = FastAPI(title="TennisClip AI", version="0.1.0")
# 供路由层（如 upload 路由）取全局 config，避免与 main 循环依赖
app.state.config = config

# 上传路由（两步上传 + MD5 秒传 + 分片续传）
app.include_router(upload_router.router)

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
    """健康检查：provider 块反映配置 KV 引用的生效服务商，DB 不可用时回退静态配置。"""
    from app.services import config_service

    try:
        with db_service.session() as s:
            ai = config_service.get_ai_config(s, config)
        provider = {
            "name": ai.provider,
            "model": ai.model,
            "base_url": ai.base_url,
            "api_key_set": bool(ai.api_key),
            "source": ai.source,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("health: 提供商解析回退静态配置：{}", exc)
        try:
            p = config.active_provider
            provider = {
                "name": "custom",
                "model": p.model or (p.models[0] if p.models else ""),
                "base_url": p.base_url,
                "api_key_set": bool(config.api_key),
                "source": "config",
            }
        except ValueError as exc2:
            logger.warning("health: 无法解析静态 provider：{}", exc2)
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


def _process_one(
    video_path: Path,
    task_id: str,
    level: str = "intermediate",
    result: Optional[TaskResult] = None,
) -> TaskResult:
    if result is None:
        result = TaskResult(task_id=task_id, source_video=str(video_path))
    started = time.monotonic()
    try:
        run_pipeline(video_path, config, result, level=level)
    finally:
        result.elapsed_seconds = time.monotonic() - started
    return result


@app.post("/api/v1/process")
async def process_video(
    file: UploadFile | None = None,
    md5: str | None = Form(None),
    level: str = Form("intermediate"),
) -> dict:
    """提交处理任务。

    两种来源：
    - md5：两步上传命中/完成后，前端以已落盘视频的 md5 提交（秒传复用已上传文件）。
    - file：兼容旧的整体直传（仍按 MD5 去重落盘，便于后续秒传）。
    二者皆无则 400。
    """
    task_id = uuid.uuid4().hex[:12]

    if md5:
        with db_service.session() as s:
            rec = upload_service.find_by_md5(s, md5)
            if rec is None or not upload_service.exists_physically(config, rec):
                raise HTTPException(status_code=400, detail="md5 对应的视频不存在，请重新上传")
            video_path = config.data_path / rec.rel_path
            size_mb = rec.size_bytes / (1024 * 1024)
    elif file is not None:
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="上传文件为空，请重新选择视频")
        suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
        with db_service.session() as s:
            rec, _ = upload_service.register_file(
                s, config, data, ext=suffix, original_name=file.filename or ""
            )
            s.commit()
            video_path = config.data_path / rec.rel_path
            size_mb = rec.size_bytes / (1024 * 1024)
    else:
        raise HTTPException(status_code=400, detail="缺少 file 或 md5 参数")

    # 落库：上传文件记录
    db_service.record_task_output(task_id, "uploaded", str(video_path), size_mb)

    queue.submit(lambda r: _process_one(video_path, task_id, level, result=r), task_id=task_id)
    return {"task_id": task_id, "status": TaskStatus.PENDING.value}


def _get_result(task_id: str) -> TaskResult:
    """从任务队列取结果；任务不存在（如 id 拼写错误或已过期）返回 404 而非 500。"""
    try:
        return queue.get(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="task not found")


@app.get("/api/v1/tasks/{task_id}")
def get_task(task_id: str) -> dict:
    result = _get_result(task_id)
    return result.model_dump()


@app.get("/api/v1/tasks/{task_id}/report")
def get_report(task_id: str) -> FileResponse:
    result = _get_result(task_id)
    if not result.report_path:
        raise HTTPException(404, "report not ready")
    return FileResponse(result.report_path, filename=result.report_path.split("/")[-1])


@app.get("/api/v1/tasks/{task_id}/video")
def get_video(task_id: str) -> FileResponse:
    result = _get_result(task_id)
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
    """模型服务商列表（数据库动态配置）；api_key 已掩码，含 is_selected。"""
    return db_service.list_providers()


@app.post("/api/v1/db/providers")
def create_provider(payload: dict) -> dict:
    """新增模型服务商（name/base_url/api_key/models 必填，enabled/sort_order 可选）。"""
    try:
        return db_service.create_provider(
            name=payload.get("name", ""),
            base_url=payload.get("base_url", ""),
            api_key=payload.get("api_key", "") or "",
            models=payload.get("models", []) or [],
            enabled=bool(payload.get("enabled", True)),
            sort_order=int(payload.get("sort_order", 0) or 0),
        )
    except db_service.ProviderConflict as exc:
        raise HTTPException(409, str(exc))
    except db_service.ProviderNotFound as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.put("/api/v1/db/providers/{provider_id}")
def update_provider(provider_id: int, payload: dict) -> dict:
    """编辑模型服务商（按 id 定位；api_key 留空表示保留原值）。"""
    try:
        return db_service.update_provider(
            provider_id=provider_id,
            name=payload.get("name", ""),
            base_url=payload.get("base_url", ""),
            api_key=payload.get("api_key", "") or "",
            models=payload.get("models", []) or [],
            enabled=bool(payload.get("enabled", True)),
            sort_order=int(payload.get("sort_order", 0) or 0),
        )
    except db_service.ProviderConflict as exc:
        raise HTTPException(409, str(exc))
    except db_service.ProviderNotFound as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.delete("/api/v1/db/providers/{provider_id}")
def delete_provider(provider_id: int) -> dict:
    """删除模型服务商；被当前 ai.provider 直选引用的服务商返回 409。"""
    try:
        db_service.delete_provider(provider_id)
    except db_service.ProviderConflict as exc:
        raise HTTPException(409, str(exc))
    except db_service.ProviderNotFound as exc:
        raise HTTPException(404, str(exc))
    return {"ok": True, "deleted": provider_id}


@app.post("/api/v1/db/providers/check-models")
async def check_models(payload: dict) -> dict:
    """校验模型可用性：list（GET /models）或逐模型 probe（chat/completions）。"""
    from app.services import provider_probe

    try:
        return await provider_probe.check_models(
            base_url=payload.get("base_url", "") or "",
            api_key=payload.get("api_key", "") or "",
            models=payload.get("models", []) or [],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("check-models 失败: {}", exc)
        raise HTTPException(500, f"校验失败: {exc}")


# ---------- 配置 KV 端点（ai.provider / ai.model 等覆盖） ----------

@app.get("/api/v1/config")
def config_list() -> list[dict]:
    """配置项列表（DB 覆盖 > 默认值），api_key 掩码，select 项含动态选项。"""
    from app.services import config_service

    with db_service.session() as s:
        return config_service.build_config_list(s, config)


@app.put("/api/v1/config/{key}")
def config_set(key: str, payload: dict) -> dict:
    """设置配置覆盖（ai.provider 切换服务商、ai.model 覆盖模型）。"""
    from app.services import config_service

    with db_service.session() as s:
        return config_service.set_config_value(s, key, payload.get("value", "") or "")


@app.delete("/api/v1/config/{key}")
def config_delete(key: str) -> dict:
    """删除配置覆盖（恢复默认值）。"""
    from app.services import config_service

    with db_service.session() as s:
        return config_service.delete_config_value(s, key, config)


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
