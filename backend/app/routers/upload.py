"""上传相关路由：MD5 预检秒传 + 分片上传 + 断点续传。

挂载于 /api/v1/upload（见 app.main 注册）。所有物理写盘/读盘统一经
app.services.upload_service 门面，路由层不自行拼路径。

两步上传模型（参考 TennisDiary）：
  1) POST /check {md5, size_bytes} → {hit, chunk?}：命中则前端跳过上传（秒传）
  2) 未命中：POST /chunk 逐片上传 → GET /chunks 查进度（续传）→ POST /complete 合并登记
  之后前端用返回的 md5 调 POST /api/v1/process {md5, level} 进入分析。
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel

from app.config import AppConfig
from app.services import db_service, upload_service
from app.services.upload_service import (
    ChunkIncompleteError,
    ChunkMismatchError,
    ChunkParamError,
    ChunkSessionError,
    ChunkTooLargeError,
    ChunkVerifyError,
)

router = APIRouter(prefix="/api/v1/upload", tags=["upload"])


def get_config(request: Request) -> AppConfig:
    """从 app.state 取全局 config（启动时注入，避免与 main 循环依赖）。"""
    return request.app.state.config


class CheckRequest(BaseModel):
    md5: str
    size_bytes: int = 0


class CompleteRequest(BaseModel):
    md5: str
    size_bytes: int = 0


def _chunk_error(exc: Exception) -> HTTPException:
    """分片异常 → HTTP 异常：参数/会话/校验 400、MD5 不符 409、超限 413。"""
    if isinstance(exc, ChunkMismatchError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, ChunkTooLargeError):
        return HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/check")
def check_upload(payload: CheckRequest, config: AppConfig = Depends(get_config)) -> dict:
    """秒传预检（MD5 精确匹配）。

    - 命中（库有记录 + 物理文件存在 + size 一致）→ {hit: true}
    - 未命中 → {hit: false, chunk: {策略 + 现有会话进度}}（供断点续传）
    """
    md5 = payload.md5 or ""
    with db_service.session() as db:
        hit = upload_service.check_md5(db, config, md5, payload.size_bytes)
    if hit:
        return {"hit": True}
    # 未命中也下发分片策略与已存在会话进度（如有），前端据此断点续传
    return {"hit": False, "chunk": upload_service.chunk_status(config, md5)}


@router.post("/chunk")
async def upload_chunk(
    md5: str = Form(...),
    index: int = Form(...),
    total: int = Form(default=0),
    size_bytes: int = Form(default=0),
    original_name: str = Form(default=""),
    crc32: str = Form(default=""),
    length: int = Form(default=0),
    file: UploadFile = File(...),
    config: AppConfig = Depends(get_config),
) -> dict:
    """上传一个视频分片。

    - 首片（或会话缺失时）带 total / size_bytes / original_name 建立会话（幂等）
    - 单片按 offset=index*chunk_size 定位写入 data.bin；写盘 + crc32 校验通过才入账 ok
    - 校验失败入账 failed 并返回 400，客户端只需重传该片
    """
    try:
        if total > 0 and size_bytes > 0:
            upload_service.open_chunk_session(
                config, md5, total_size=size_bytes, total=total, original_name=original_name
            )
        content = await file.read()
        result = upload_service.register_chunk(
            config, md5, index, content, length=length, crc32=crc32
        )
    except (
        ChunkParamError,
        ChunkSessionError,
        ChunkVerifyError,
    ) as exc:
        raise _chunk_error(exc) from exc
    except Exception as exc:
        from app.utils.logger import get_logger

        log = get_logger(__name__)
        log.exception("视频分片上传异常: md5={} index={} err={}", md5[:12], index, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="分片上传失败，请重试"
        ) from exc
    return result


@router.get("/chunks")
def list_chunks(md5: str, config: AppConfig = Depends(get_config)) -> dict:
    """查询分片会话进度（断点续传依据）：ok / failed / missing。"""
    try:
        summary = upload_service.list_chunks(config, md5)
    except ChunkParamError as exc:
        raise _chunk_error(exc) from exc
    return summary


@router.post("/complete")
def complete_upload(payload: CompleteRequest, config: AppConfig = Depends(get_config)) -> dict:
    """分片上传完成：段齐全校验 → 整文件 MD5/size → 登记为受管视频。

    免合并：data.bin 即最终文件，直接迁入 uploads/{md5}{ext} 并落库。
    返回 {md5, rel_path, ext, original_name} 供 /api/v1/process 消费。
    """
    try:
        with db_service.session() as db:
            rec, reused = upload_service.complete_chunk_upload(
                db, config, payload.md5, size_bytes=payload.size_bytes
            )
            db.commit()
    except (
        ChunkParamError,
        ChunkSessionError,
        ChunkIncompleteError,
        ChunkMismatchError,
        ChunkTooLargeError,
    ) as exc:
        raise _chunk_error(exc) from exc
    except Exception as exc:
        from app.utils.logger import get_logger

        log = get_logger(__name__)
        log.exception("分片合并登记异常: md5={} err={}", payload.md5[:12], exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="视频上传失败，请重试"
        ) from exc
    return {
        "md5": rec.md5,
        "rel_path": rec.rel_path,
        "ext": rec.ext,
        "original_name": rec.original_name,
        "reused": reused,
    }
