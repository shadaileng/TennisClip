"""高光识别：调用 Step 3.7 Flash 多模态理解，输出结构化时间戳。"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Optional

from app.config import AppConfig
from app.models import HighlightResult, Segment
from app.utils import llm
from app.utils.logger import get_logger

from prompts.highlight_analysis import build_highlight_prompt

logger = get_logger(__name__)


def find_highlights(
    video_path: Path,
    config: AppConfig,
    duration_seconds: Optional[float] = None,
    level: str = "intermediate",
) -> HighlightResult:
    """识别高光回合，返回结构化时间戳。

    流程：构造领域 Prompt → 调用 LLM（自动 Mock 回退）→ 解析 JSON。
    """
    logger.info("highlight: start for %s", video_path.name)
    prompt = build_highlight_prompt(level=level, duration_seconds=duration_seconds)

    result = llm.complete_structured(
        video_path=video_path,
        prompt=prompt,
        config=config,
        schema_hint="HighlightResult",
    )
    if result is None:
        raise RuntimeError("LLM 未返回结构化结果")

    highlight = HighlightResult(**result)
    highlight.target_duration = config.highlight.target_duration
    _clamp_segments(highlight, config, duration_seconds)
    logger.info("highlight: %d segments", len(highlight.segments))
    return highlight


def _clamp_segments(highlight: HighlightResult, config: AppConfig, duration: Optional[float]) -> None:
    """裁剪非法时间戳，保证在 [0, duration] 内且时长 >= min_segment_seconds。"""
    if not duration:
        return
    kept = []
    for seg in highlight.segments:
        start = max(0.0, seg.start)
        end = min(duration, max(seg.end, start + config.highlight.min_segment_seconds))
        if end > start:
            kept.append(Segment(start=start, end=end, label=seg.label, confidence=seg.confidence))
    kept.sort(key=lambda s: s.start)
    if len(kept) > config.highlight.max_segments:
        kept = [max(kept, key=lambda s: s.confidence)] + kept[:-1][: config.highlight.max_segments - 1]
        kept.sort(key=lambda s: s.start)
    highlight.segments = kept


def mock_highlights(duration_seconds: float, config: AppConfig, seed: Optional[int] = None) -> HighlightResult:
    """Mock 高光：按均匀分布生成 3 个候选片段（开发态使用）。"""
    rnd = random.Random(seed)
    total = max(duration_seconds, config.highlight.min_segment_seconds * config.highlight.max_segments)
    segments = []
    for i in range(config.highlight.max_segments):
        start = total * (i + 1) / (config.highlight.max_segments + 1) + rnd.uniform(-2, 2)
        end = start + config.highlight.min_segment_seconds + 1
        segments.append(
            Segment(
                start=max(0.0, round(start, 2)),
                end=min(total, round(end, 2)),
                label=["ace", "rally", "winner"][i % 3],
                confidence=round(rnd.uniform(0.85, 0.99), 2),
            )
        )
    return HighlightResult(
        segments=segments,
        target_duration=config.highlight.target_duration,
        scene_type="training",
        reasoning="mock: uniform sampling",
    )
