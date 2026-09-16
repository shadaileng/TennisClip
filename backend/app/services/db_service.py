"""数据库服务层：任务全生命周期持久化（多兼容 SQLite/Postgres/MySQL）。

在核心流水线的各节点调用本模块，把任务/输入/输出/结果快照/文件记录落库。
所有方法都 try/except 包裹：DB 不可用时记录日志但不阻断主流程
（对应需求文档 4.1 风险2「链路卡顿」的容错设计——DB 故障降级为内存队列）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.config import AppConfig
from app.db import _apply_migrations_or_create
from app.db_models import (
    Base,
    FileRecord,
    ModelProvider,
    Task,
    TaskInput,
    TaskOutput,
    TaskResult,
    _seed_providers,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

_engine = None
_SessionLocal: Optional[sessionmaker] = None


def init_db(config: AppConfig) -> None:
    """初始化引擎与表结构（幂等），并导入 model_providers 种子。"""
    global _engine, _SessionLocal
    if _engine is not None:
        return

    url = config.database.url
    # SQLite 时确保父目录存在
    if url.startswith("sqlite"):
        db_path = url.split("sqlite:///", 1)[-1]
        if not db_path.startswith("/") and not db_path.startswith("\\"):
            target = config.data_path
            target.mkdir(parents=True, exist_ok=True)

    kwargs: dict = {"echo": config.database.echo}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        from sqlalchemy.pool import StaticPool
        kwargs["poolclass"] = StaticPool

    _engine = create_engine(url, **kwargs)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)

    # 优先 Alembic 迁移，create_all 兜底（对无 alembic_version 的库自动 stamp head）
    _apply_migrations_or_create(_engine)

    # 导入 model_providers 种子（若表为空）
    with _SessionLocal() as session:
        if session.query(ModelProvider).count() == 0:
            session.add_all(_seed_providers())
            session.commit()
            logger.info("db: seeded {} model providers", len(config.llm.providers))

    logger.info("db: initialized ({})", _mask(url))
    return None


def _mask(url: str) -> str:
    """脱敏连接串（隐藏密码）。"""
    if "@" in url and "://" in url:
        head, tail = url.split("@", 1)
        scheme, _ = head.split("://", 1)
        return f"{scheme}://***@" + tail
    return url


def session() -> Session:
    if _SessionLocal is None:
        raise RuntimeError("DB 未初始化，请先调用 init_db(config)")
    return _SessionLocal()


# ---------- 任务生命周期 ----------

def record_task_start(task_id: str, source_video: str, level: str) -> None:
    """记录任务创建（输入信息随后单独写 task_inputs）。"""
    try:
        with session() as s:
            s.add(Task(task_id=task_id, status="pending", level=level, source_video=source_video))
            s.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("db: record_task_start failed: {}", exc)


def record_task_input(
    task_id: str,
    video_path: Path,
    duration_seconds: Optional[float],
    width: Optional[int],
    height: Optional[int],
    fps: Optional[float],
    level: str,
) -> None:
    try:
        size_mb = video_path.stat().st_size / (1024 * 1024) if video_path.exists() else 0.0
        with session() as s:
            s.add(
                TaskInput(
                    task_id=task_id,
                    file_path=str(video_path),
                    file_name=video_path.name,
                    file_size_mb=round(size_mb, 3),
                    duration_seconds=duration_seconds,
                    width=width,
                    height=height,
                    fps=fps,
                    level=level,
                )
            )
            s.add(FileRecord(file_path=str(video_path), kind="input", task_id=task_id, size_mb=round(size_mb, 3)))
            s.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("db: record_task_input failed: {}", exc)


def record_task_finish(
    task_id: str,
    status: str,
    elapsed_seconds: float,
    error: Optional[str],
    highlight_json: Optional[dict],
    report_json: Optional[dict],
    generated_by: str,
) -> None:
    """记录任务终态 + 结果快照。"""
    try:
        with session() as s:
            task = s.query(Task).filter_by(task_id=task_id).first()
            if task:
                task.status = status
                task.elapsed_seconds = elapsed_seconds
                task.error = error
            s.add(
                TaskResult(
                    task_id=task_id,
                    highlight_json=json.dumps(highlight_json, ensure_ascii=False) if highlight_json else None,
                    report_json=json.dumps(report_json, ensure_ascii=False) if report_json else None,
                    generated_by=generated_by,
                )
            )
            s.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("db: record_task_finish failed: {}", exc)


def record_task_output(task_id: str, kind: str, file_path: str, size_mb: float, target_duration: Optional[int] = None) -> None:
    try:
        with session() as s:
            s.add(
                TaskOutput(
                    task_id=task_id,
                    kind=kind,
                    file_path=file_path,
                    file_size_mb=round(size_mb, 3),
                    target_duration=target_duration,
                )
            )
            s.add(FileRecord(file_path=file_path, kind=kind, task_id=task_id, size_mb=round(size_mb, 3)))
            s.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("db: record_task_output failed: {}", exc)


def mark_file_removed(file_path: str, task_id: Optional[str] = None) -> None:
    """文件管理：标记某文件已删除。"""
    try:
        with session() as s:
            rec = (
                s.query(FileRecord)
                .filter_by(file_path=file_path, kind="preprocessed")
                .first()
            )
            if rec:
                from datetime import datetime
                rec.status = "removed"
                rec.removed_at = datetime.utcnow()
            s.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("db: mark_file_removed failed: {}", exc)


# ---------- 模型提供商（数据库动态切换） ----------

def list_providers() -> list[ModelProvider]:
    try:
        with session() as s:
            return s.query(ModelProvider).all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("db: list_providers failed: {}", exc)
        return []


def activate_provider(name: str) -> bool:
    """把指定 name 的 provider 设为 active（其他置 false）。"""
    try:
        with session() as s:
            for p in s.query(ModelProvider).all():
                p.is_active = (p.name == name)
            s.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("db: activate_provider failed: {}", exc)
        return False


def get_active_provider() -> Optional[ModelProvider]:
    """返回当前生效（is_active=True）的提供商；无激活记录时取第一条；DB 故障时返回 None。

    这是「运行时动态切换」的单一事实来源：/health、启动自检、LLM 调用均优先读此记录，
    DB 不可用时由调用方回退静态 config.active_provider。
    """
    try:
        with session() as s:
            p = s.query(ModelProvider).filter_by(is_active=True).first()
            if p is None:
                p = s.query(ModelProvider).first()
            return p
    except Exception as exc:  # noqa: BLE001
        logger.warning("db: get_active_provider failed: {}", exc)
        return None


# ---------- 查询 ----------

def get_task_history(limit: int = 50) -> list[Task]:
    """最近任务记录（管理/审计用）。"""
    try:
        with session() as s:
            return s.query(Task).order_by(Task.created_at.desc()).limit(limit).all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("db: get_task_history failed: {}", exc)
        return []
