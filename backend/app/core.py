"""核心流水线：预处理 → 高光识别 → 剪辑合成 → 报告生成。

每步节点同步写库（app.services.db_service），记录任务/输入/输出/结果快照/文件。
DB 不可用时自动降级（不阻断主流程）。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from app.config import AppConfig
from app.config_registry import DEFAULT_STAGES
from app.models import TaskResult, TaskStatus
from app.services import db_service
from app.services import highlight as highlight_service
from app.services import preprocess, report, video_editor
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _validate_enabled_stages(enabled: set[str]) -> None:
    """校验阶段依赖：预处理为前置、剪辑/报告依赖高光识别；非法组合抛清晰错误。"""
    if not enabled:
        return
    if ("highlight" in enabled or "edit" in enabled or "report" in enabled) and "preprocess" not in enabled:
        raise ValueError(
            "管线依赖非法：高光识别/剪辑/报告 依赖 预处理，启用前须先启用预处理阶段"
        )
    if ("edit" in enabled or "report" in enabled) and "highlight" not in enabled:
        raise ValueError(
            "管线依赖非法：剪辑/报告 依赖 高光识别，启用前须先启用高光识别阶段"
        )


def run_pipeline(
    video_path: Path,
    config: AppConfig,
    result: TaskResult,
    level: str = "intermediate",
    analysis_mode: Optional[str] = None,
    enabled_stages: Optional[list] = None,
) -> TaskResult:
    """全链路处理一条视频，结果写入传入的 TaskResult，并同步落库。

    enabled_stages：启用的管线阶段（默认全开全序）；顺序固定为
        [preprocess, highlight, edit, report]，仅按集合启用/停用。
    analysis_mode：高光识别媒体输入策略（frame/video）；None 时回落 config.llm.analysis_mode。
    """
    started = time.monotonic()
    task_id = result.task_id

    # 每任务独立输出目录：产物按 task_id 隔离，避免同内容（同 MD5）重复分析时互相覆盖
    task_out = config.ensure_output_dir() / task_id
    task_out.mkdir(parents=True, exist_ok=True)

    if enabled_stages is None:
        enabled_stages = list(DEFAULT_STAGES)
    enabled = set(enabled_stages)

    # 记录任务开始 + 输入
    db_service.record_task_start(task_id, str(video_path), level)

    try:
        logger.info(
            "pipeline: start {} (level={} mode={} stages={})",
            video_path.name, level, analysis_mode, sorted(enabled),
        )
        _validate_enabled_stages(enabled)

        processed_video = video_path
        duration = 60.0

        # 1. 预处理
        if "preprocess" in enabled:
            result.stage = "preprocessing"
            logger.info("pipeline: stage=preprocessing {}", video_path.name)
            processed_video = preprocess.preprocess(video_path, config, work_dir=task_out)
            meta = preprocess.probe_video(processed_video)
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
        else:
            logger.info("pipeline: 预处理阶段已停用，高光识别将直接使用原始视频")

        # 2. 高光识别
        if "highlight" in enabled:
            result.stage = "highlighting"
            logger.info("pipeline: stage=highlighting {}", video_path.name)
            hl = highlight_service.find_highlights(
                processed_video, config, duration, level=level, analysis_mode=analysis_mode
            )
            result.highlight = hl

        # 3. 剪辑合成
        if "edit" in enabled:
            if result.highlight is None:
                raise RuntimeError("剪辑阶段依赖高光识别结果，但高光识别未产出")
            result.stage = "editing"
            logger.info("pipeline: stage=editing {}", video_path.name)
            hl_video = video_editor.edit_highlight_video(processed_video, result.highlight, config)
            result.highlight_video_path = str(hl_video)

            # 落库：输出 - 集锦视频
            db_service.record_task_output(
                task_id, "highlight_video", str(hl_video),
                hl_video.stat().st_size / (1024 * 1024),
                target_duration=config.highlight.target_duration,
            )

        # 4. 技术分析报告（非致命：失败仅缺失报告，不丢弃已生成的高光视频）
        #    all 档位（所有高光回合）仅做剪辑拼接，不调用 LLM 生成技术分析报告
        if "report" in enabled:
            if result.highlight is None:
                raise RuntimeError("报告阶段依赖高光识别结果，但高光识别未产出")
            if result.highlight.all_highlights:
                logger.info("pipeline: all 模式仅剪辑，跳过技术分析报告 {}", video_path.name)
                result.report = None
                result.report_path = None
            else:
                result.stage = "reporting"
                logger.info("pipeline: stage=reporting {}", video_path.name)
                report_file = task_out / "report.json"
                try:
                    result.report = report.generate_report(
                        processed_video, result.highlight, config, level=level, out_path=report_file
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
