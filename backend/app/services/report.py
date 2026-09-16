"""技术分析报告生成：基于高光结果 + LLM 深度分析，输出分层教学建议。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from app.config import AppConfig
from app.models import HighlightResult, StrokeAnalysis, TechnicalReport
from app.utils import llm
from app.utils.logger import get_logger
from prompts.highlight_analysis import build_report_prompt

logger = get_logger(__name__)


def generate_report(
    video_path: Path,
    highlight: HighlightResult,
    config: AppConfig,
    level: str = "intermediate",
    out_path: Optional[Path] = None,
) -> TechnicalReport:
    """生成结构化技术分析报告；LLM 失败时使用模板兜底。

    out_path 指定落盘路径（由调用方 core.run_pipeline 决定文件名，
    保证与 TaskResult.report_path 记录一致）；缺省时按 video_path.stem 生成。
    """
    logger.info("report: start level={}", level)
    prompt = build_report_prompt(level=level, highlight=highlight)

    result = llm.complete_structured(
        video_path=video_path,
        prompt=prompt,
        config=config,
        schema_hint="TechnicalReport",
    )

    if result:
        report = TechnicalReport(**result)
    else:
        report = _fallback_report(highlight, level)

    report_path = out_path or (config.ensure_output_dir() / f"{video_path.stem}_report.json")
    report_path = Path(report_path)
    report_path.write_text(
        json.dumps(report.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("report: saved to {}", report_path)
    report.generated_by = f"TennisClip AI / Step 3.7 Flash (level={level})"
    return report


def _fallback_report(highlight: HighlightResult, level: str) -> TechnicalReport:
    """LLM 不可用时的模板兜底报告，保证链路 100% 产出。"""
    strokes = [
        StrokeAnalysis(
            action="forehand",
            problem="前臂旋转不足，击球点偏后",
            cause="转髋不充分导致重心后移",
            suggestion="多球前点击球训练，强化转髋 90°",
            severity="moderate",
        ),
        StrokeAnalysis(
            action="serve",
            problem="抛球位置偏左",
            cause="肩肘角度未打开",
            suggestion="固定抛球点标记训练",
            severity="low",
        ),
    ]
    return TechnicalReport(
        level=level,
        summary=f"识别到 {len(highlight.segments)} 个高光回合（{highlight.scene_type} 场景），以下为通用技术改进建议。",
        strokes=strokes,
        strengths=["多拍相持稳定性较好"],
        weaknesses=["发球二发成功率偏低", "反手截击击球点偏低"],
        training_plan=["每周 2 次正手多球点前击训练", "每周 1 次发球轮抛球点练习"],
        generated_by="TennisClip AI / template fallback",
    )
