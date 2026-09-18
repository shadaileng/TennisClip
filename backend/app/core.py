"""核心流水线：预处理 → 高光识别 → 剪辑合成 → 报告生成。

每步节点同步写库（app.services.db_service），记录任务/输入/输出/结果快照/文件。
DB 不可用时自动降级（不阻断主流程）。
"""

from __future__ import annotations

import time
from pathlib import Path

from app.config import AppConfig
from app.models import TaskResult, TaskStatus
from app.services import db_service
from app.services import highlight as highlight_service
from app.services import preprocess, report, video_editor
from app.utils.logger import get_logger

logger = get_logger(__name__)


def run_pipeline(
    video_path: Path,
    config: AppConfig,
    result: TaskResult,
    level: str = "intermediate",
) -> TaskResult:
    """全链路处理一条视频，结果写入传入的 TaskResult，并同步落库。"""
    started = time.monotonic()
    task_id = result.task_id

    # 记录任务开始 + 输入
    db_service.record_task_start(task_id, str(video_path), level)

    try:
        logger.info("pipeline: start {} (level={})", video_path.name, level)

        # 1. 预处理
        result.stage = "preprocessing"
        logger.info("pipeline: stage=preprocessing {}", video_path.name)
        preprocessed = preprocess.preprocess(video_path, config)
        meta = preprocess.probe_video(preprocessed)
        duration = meta.get("duration") or 60.0

        # 落库：输入元信息
        db_service.record_task_input(
            task_id=task_id,
            video_path=video_path,
            duration_seconds=meta.get("duration"),
            width=meta.get("width"),
            height=meta.get("height"),
            fps=meta.get("fps"),
            level=level,
        )

        # 2. 高光识别
        result.stage = "highlighting"
        logger.info("pipeline: stage=highlighting {}", video_path.name)
        hl = highlight_service.find_highlights(preprocessed, config, duration, level=level)
        result.highlight = hl

        # 3. 剪辑合成
        result.stage = "editing"
        logger.info("pipeline: stage=editing {}", video_path.name)
        hl_video = video_editor.edit_highlight_video(preprocessed, hl, config)
        result.highlight_video_path = str(hl_video)

        # 落库：输出 - 集锦视频
        db_service.record_task_output(
            task_id, "highlight_video", str(hl_video),
            hl_video.stat().st_size / (1024 * 1024),
            target_duration=config.highlight.target_duration,
        )

        # 4. 技术分析报告（非致命：失败仅缺失报告，不丢弃已生成的高光视频）
        result.stage = "reporting"
        logger.info("pipeline: stage=reporting {}", video_path.name)
        report_file = config.ensure_output_dir() / f"{video_path.stem}_report.json"
        try:
            result.report = report.generate_report(
                preprocessed, hl, config, level=level, out_path=report_file
            )
            result.report_path = str(report_file)
            # 落库：输出 - 报告文件
            if report_file.exists():
                db_service.record_task_output(
                    task_id, "report", str(report_file),
                    report_file.stat().st_size / (1024 * 1024),
                )
        except Exception as exc:  # noqa: BLE001
            result.report = None
            result.report_path = None
            result.report_error = str(exc)
            logger.warning("pipeline: 报告生成失败（保留高光视频）: {}", exc)

        result.status = TaskStatus.SUCCEEDED
        result.elapsed_seconds = time.monotonic() - started
        logger.info("pipeline: done in {:.1f}s", result.elapsed_seconds)
    except Exception as exc:  # noqa: BLE001
        result.status = TaskStatus.FAILED
        result.error = str(exc)
        result.elapsed_seconds = time.monotonic() - started
        logger.exception("pipeline failed: {}", exc)

    # 落库：任务终态 + 结果快照
    db_service.record_task_finish(
        task_id=task_id,
        status=result.status,
        elapsed_seconds=result.elapsed_seconds,
        error=result.error,
        highlight_json=result.highlight.model_dump() if result.highlight else None,
        report_json=result.report.model_dump() if result.report else None,
        generated_by=result.report.generated_by if result.report else "",
    )

    return result
