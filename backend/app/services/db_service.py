"""数据库服务层：任务全生命周期持久化（多兼容 SQLite/Postgres/MySQL）。

在核心流水线的各节点调用本模块，把任务/输入/输出/结果快照/文件记录落库。
所有方法都 try/except 包裹：DB 不可用时记录日志但不阻断主流程
（对应需求文档 4.1 风险2「链路卡顿」的容错设计——DB 故障降级为内存队列）。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.config import AppConfig
from app.db import _apply_migrations_or_create
from app.db_models import (
    AiProvider,
    Base,
    FileRecord,
    SystemConfig,
    Task,
    TaskInput,
    TaskOutput,
    TaskResult,
    UploadedVideo,
    _seed_providers,
    _seed_system_config,
)
from app.services import config_service
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

    # 导入 ai_providers / system_config 种子（若表为空）
    with _SessionLocal() as session:
        from app.config import load_config

        if session.query(AiProvider).count() == 0:
            session.add_all(_seed_providers(config))
            session.commit()
            logger.info("db: seeded {} ai providers", len(config.llm.providers))
        if session.query(SystemConfig).count() == 0:
            _seed_system_config(config, session)
            session.commit()
            logger.info("db: seeded system config")

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
    """记录任务创建（输入信息随后单独写 task_inputs）。

    幂等：task 行已存在（恢复续跑场景）则仅更新 status=processing，不 INSERT；
    避免 UNIQUE constraint failed: tasks.task_id 告警。
    """
    try:
        with session() as s:
            existing = s.query(Task).filter_by(task_id=task_id).first()
            if existing is not None:
                existing.status = "processing"
                existing.updated_at = datetime.utcnow()
            else:
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


def list_stale_tasks() -> list[dict]:
    """列出遗留的未终态任务（pending/processing/timeout），供重启恢复决策。

    返回 [{task_id, status, level, source_video, created_at}]，按创建时间升序。
    """
    try:
        with session() as s:
            rows = s.query(Task).filter(
                Task.status.in_(["pending", "processing", "timeout"])
            ).order_by(Task.created_at.asc()).all()
            return [
                {
                    "task_id": t.task_id,
                    "status": t.status,
                    "level": t.level,
                    "source_video": t.source_video,
                    "created_at": t.created_at,
                }
                for t in rows
            ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("db: list_stale_tasks failed: {}", exc)
        return []


def mark_task_failed(task_id: str, error: str = "service restarted, in-flight task was interrupted") -> bool:
    """将单个任务标记为 failed（服务重启兜底用）。

    返回是否实际更新了行。
    """
    try:
        with session() as s:
            t = s.query(Task).filter_by(task_id=task_id).first()
            if t is None:
                return False
            t.status = "failed"
            t.error = error
            t.updated_at = datetime.utcnow()
            s.commit()
            return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("db: mark_task_failed failed: {}", exc)
        return False


def recover_stale_tasks() -> list[dict]:
    """服务重启后恢复非终态任务（pending/processing/timeout）。

    逐个任务尝试：从 task_outputs(kind="uploaded") 反解输入 MD5，确认物理文件
    与 uploaded_videos 记录仍在 → 可恢复（收集 task_id/level/input_md5 供重新入队）；
    文件丢失或无法解析 → 标记 failed。
    返回可恢复任务列表 [{task_id, level, source_video, input_md5}]（按创建时间升序）。
    调用方（main.py 恢复钩子）应基于 input_md5 做同视频去重，仅保留每组最新任务。
    """
    recoverable: list[dict] = []
    try:
        with session() as s:
            stale = (
                s.query(Task)
                .filter(Task.status.in_(["pending", "processing", "timeout"]))
                .order_by(Task.created_at.asc())
                .all()
            )
        if not stale:
            return recoverable

        for task in stale:
            md5, rel_path = _resolve_input_md5(task.task_id)
            if md5:
                rec = _find_uploaded(md5)
                if rec is not None and _file_exists(rec):
                    recoverable.append({
                        "task_id": task.task_id,
                        "level": task.level,
                        "source_video": task.source_video,
                        "input_md5": md5,
                    })
                    continue
            # 输入不可用 → 标记 failed
            _mark_task_failed(task.task_id, "任务在服务重启时中断，且输入视频不可用（文件缺失或未登记）")
    except Exception as exc:  # noqa: BLE001
        logger.warning("db: recover_stale_tasks failed: {}", exc)
    return recoverable


def _resolve_input_md5(task_id: str) -> tuple[Optional[str], Optional[str]]:
    """从 task_outputs(kind="uploaded") 反解输入 MD5 与 rel_path。

    物理文件名 = {md5}{ext}（upload_service 约定），取首个 32 位十六进制段。
    """
    with session() as s:
        out = (
            s.query(TaskOutput)
            .filter_by(task_id=task_id, kind="uploaded")
            .order_by(TaskOutput.created_at.asc())
            .first()
        )
    if out is None:
        return None, None
    name = Path(out.file_path).name
    m = re.match(r"^([0-9a-f]{32})", name)
    if not m:
        return None, None
    md5 = m.group(1)
    rec = _find_uploaded(md5)
    rel_path = rec.rel_path if rec else None
    return md5, rel_path


def _find_uploaded(md5: str) -> Optional[UploadedVideo]:
    with session() as s:
        return s.query(UploadedVideo).filter_by(md5=md5).first()


def _file_exists(rec: UploadedVideo) -> bool:
    try:
        from app.config import load_config
        cfg = load_config()
    except Exception:  # noqa: BLE001
        return False
    return (cfg.data_path / rec.rel_path).exists()


def _mark_task_failed(task_id: str, error: str) -> None:
    with session() as s:
        task = s.query(Task).filter_by(task_id=task_id).first()
        if task:
            task.status = "failed"
            task.error = error
            task.updated_at = datetime.utcnow()
        s.commit()


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


# ---------- 模型提供商（数据库动态切换 + 多模型管理） ----------

class ProviderNotFound(ValueError):
    """指定的提供商不存在。"""


class ProviderConflict(ValueError):
    """操作与现有状态冲突（如名称重复、删除激活项）。"""


def mask_secret(value: Optional[str]) -> str:
    """掩码敏感串（对齐 TennisDiary：前3 + 末4）。委托 config_service。"""
    return config_service.mask_secret(value or "")


def _normalize_models(models) -> list[str]:
    """清洗模型列表：逐项 trim、去空、去重保序。"""
    seen: set[str] = set()
    out: list[str] = []
    for m in models or []:
        m = str(m).strip()
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def _provider_to_dict(p: AiProvider, mask: bool = True, selected_name: str = "") -> dict:
    """将 ORM 行转为接口 dict（mask=True 时 api_key 掩码；is_selected 由 ai.provider 计算）。"""
    return {
        "id": p.id,
        "name": p.name,
        "base_url": p.base_url,
        "api_key": mask_secret(p.api_key) if mask else (p.api_key or ""),
        "models": p.models,
        "default_model": p.default_model,
        "enabled": bool(p.enabled),
        "sort_order": p.sort_order,
        "is_selected": p.name == selected_name,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def list_providers() -> list[dict]:
    """返回全部服务商（按 sort_order、name 排序），api_key 已掩码，含 is_selected。"""
    try:
        with session() as s:
            selected_name = config_service.get_config_value(s, "ai.provider")
            rows = s.query(AiProvider).order_by(AiProvider.sort_order, AiProvider.name).all()
            return [_provider_to_dict(p, selected_name=selected_name) for p in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("db: list_providers failed: {}", exc)
        return []


def get_provider_by_name(name: str) -> Optional[AiProvider]:
    try:
        with session() as s:
            return s.query(AiProvider).filter_by(name=name).first()
    except Exception as exc:  # noqa: BLE001
        logger.warning("db: get_provider_by_name failed: {}", exc)
        return None


def get_provider_by_id(provider_id: int) -> Optional[AiProvider]:
    try:
        with session() as s:
            return s.query(AiProvider).filter_by(id=provider_id).first()
    except Exception as exc:  # noqa: BLE001
        logger.warning("db: get_provider_by_id failed: {}", exc)
        return None


def create_provider(
    name: str,
    base_url: str,
    api_key: str,
    models: list[str],
    enabled: bool = True,
    sort_order: int = 0,
) -> dict:
    """新增服务商；返回掩码后的 dict。

    校验：name 非空且唯一、base_url 须 http(s)://、models 至少 1 项。
    """
    name = (name or "").strip()
    if not name:
        raise ProviderNotFound("服务商名称不能为空")
    if not str(base_url).strip().lower().startswith(("http://", "https://")):
        raise ValueError("base_url 须以 http:// 或 https:// 开头")
    clean_models = _normalize_models(models)
    if not clean_models:
        raise ValueError("models 至少需包含 1 个模型")

    try:
        with session() as s:
            if s.query(AiProvider).filter_by(name=name).first():
                raise ProviderConflict(f"服务商名称已存在：{name}")
            p = AiProvider(
                name=name,
                base_url=str(base_url).strip(),
                api_key=(api_key or "").strip(),
                models=clean_models,
                enabled=1 if enabled else 0,
                sort_order=int(sort_order or 0),
            )
            s.add(p)
            s.commit()
            s.refresh(p)
            selected_name = config_service.get_config_value(s, "ai.provider")
            return _provider_to_dict(p, selected_name=selected_name)
    except (ProviderNotFound, ProviderConflict, ValueError):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("db: create_provider failed: {}", exc)
        raise ValueError(f"新增服务商失败：{exc}") from exc


def update_provider(
    provider_id: int,
    name: str,
    base_url: str,
    api_key: str,
    models: list[str],
    enabled: bool = True,
    sort_order: int = 0,
) -> dict:
    """编辑服务商（按 id 定位）；api_key 为空表示保留原值。返回掩码后的 dict。"""
    name = (name or "").strip()
    if not name:
        raise ProviderNotFound("服务商名称不能为空")
    if not str(base_url).strip().lower().startswith(("http://", "https://")):
        raise ValueError("base_url 须以 http:// 或 https:// 开头")
    clean_models = _normalize_models(models)
    if not clean_models:
        raise ValueError("models 至少需包含 1 个模型")

    try:
        with session() as s:
            p = s.query(AiProvider).filter_by(id=provider_id).first()
            if p is None:
                raise ProviderNotFound(f"服务商不存在：id={provider_id}")
            if name != p.name and s.query(AiProvider).filter_by(name=name).first():
                raise ProviderConflict(f"服务商名称已存在：{name}")
            p.name = name
            p.base_url = str(base_url).strip()
            if api_key:  # 留空保留原密钥
                p.api_key = api_key.strip()
            p.models = clean_models
            p.enabled = 1 if enabled else 0
            p.sort_order = int(sort_order or 0)
            s.commit()
            s.refresh(p)
            selected_name = config_service.get_config_value(s, "ai.provider")
            return _provider_to_dict(p, selected_name=selected_name)
    except (ProviderNotFound, ProviderConflict, ValueError):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("db: update_provider failed: {}", exc)
        raise ValueError(f"编辑服务商失败：{exc}") from exc


def delete_provider(provider_id: int) -> None:
    """按 id 删除服务商；被当前 ai.provider 直选引用的服务商拒绝（409）。"""
    try:
        with session() as s:
            p = s.query(AiProvider).filter_by(id=provider_id).first()
            if p is None:
                raise ProviderNotFound(f"服务商不存在：id={provider_id}")
            selected_name = config_service.get_config_value(s, "ai.provider") or "custom"
            if selected_name == p.name:
                raise ProviderConflict(
                    f"服务商「{p.name}」正被 ai.provider 引用，请先在配置中切换服务商"
                )
            s.delete(p)
            s.commit()
    except (ProviderNotFound, ProviderConflict):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("db: delete_provider failed: {}", exc)
        raise ValueError(f"删除服务商失败：{exc}") from exc


def delete_provider_by_name(name: str) -> None:
    """按 name 删除服务商（便捷封装）。"""
    p = get_provider_by_name(name)
    if p is None:
        raise ProviderNotFound(f"服务商不存在：{name}")
    delete_provider(p.id)


# ---------- 查询 ----------

def get_task_history(limit: int = 50) -> list[Task]:
    """最近任务记录（管理/审计用）。"""
    try:
        with session() as s:
            return s.query(Task).order_by(Task.created_at.desc()).limit(limit).all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("db: get_task_history failed: {}", exc)
        return []


def get_task_detail(task_id: str) -> dict | None:
    """从数据库重建完整任务结果（替代内存队列，历史查看不依赖服务生命周期）。

    返回与 TaskResult.model_dump() 结构对齐的 dict，前端可直接消费。
    """
    try:
        with session() as s:
            task = s.query(Task).filter_by(task_id=task_id).first()
            if not task:
                return None

            result_row = s.query(TaskResult).filter_by(task_id=task_id).first()
            outputs = s.query(TaskOutput).filter_by(task_id=task_id).all()
            inp = s.query(TaskInput).filter_by(task_id=task_id).first()

            # 从 task_outputs 定位文件路径
            hl_video_path = None
            report_file_path = None
            for o in outputs:
                if o.kind == "highlight_video":
                    hl_video_path = o.file_path
                elif o.kind == "report":
                    report_file_path = o.file_path

            # 解析 JSON 结果
            highlight = None
            report = None
            if result_row:
                if result_row.highlight_json:
                    try:
                        highlight = json.loads(result_row.highlight_json)
                    except (TypeError, ValueError):
                        pass
                if result_row.report_json:
                    try:
                        report = json.loads(result_row.report_json)
                    except (TypeError, ValueError):
                        pass

            return {
                "task_id": task.task_id,
                "status": task.status,
                "level": task.level,
                "stage": "done" if task.status == "succeeded" else task.status,
                "source_video": task.source_video or (inp.file_name if inp else ""),
                "highlight": highlight,
                "report": report,
                "highlight_video_path": hl_video_path,
                "report_path": report_file_path,
                "error": task.error,
                "elapsed_seconds": task.elapsed_seconds,
                "created_at": task.created_at.isoformat() if task.created_at else None,
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning("db: get_task_detail failed for {}: {}", task_id, exc)
        return None
