"""片段操作工具：从 highlight.py 提取的共享逻辑。

节点与旧函数共用同一份逻辑，避免双份实现漂移。
"""

from __future__ import annotations

from typing import Optional

from app.config import AppConfig
from app.models import HighlightResult, Segment
from app.utils.logger import get_logger

logger = get_logger(__name__)

# 后处理安全网：这些标签代表"非有效高光"（准备段/明显失误），直接丢弃
EXCLUDED_LABELS = {"other", "out", "error", "fault", "miss", "fail"}

# 候选窗口相对 LLM 精修的允许外扩（秒），与 prompt 中"±2s"约束一致
CANDIDATE_MARGIN = 2.0


def overlaps_any(seg: Segment, spans, margin: float = 0.0) -> bool:
    """段是否与任一候选窗口重叠（允许 margin 外扩）。"""
    for (s, e) in spans:
        if seg.start <= e + margin and seg.end >= s - margin:
            return True
    return False


def exclude_prep(
    highlight: HighlightResult,
    config: AppConfig,
    candidates: list,
    excluded_labels: Optional[set[str]] = None,
) -> None:
    """准备段后过滤安全网：丢弃 other 标签、低置信度、以及与候选动作窗口不重叠的段。

    保证 video_editor 拿到的 segments 不含捡球/走位/等待等准备段（单一真相源）。
    """
    if not config.highlight.prep_exclusion:
        return
    cfg = config.highlight
    labels = excluded_labels or EXCLUDED_LABELS
    spans = [(c.start, c.end) for c in candidates]
    kept = []
    for seg in highlight.segments:
        if seg.label in labels:
            continue
        if seg.confidence < cfg.min_segment_confidence:
            continue
        if spans and not overlaps_any(seg, spans):
            continue
        kept.append(seg)
    highlight.segments = kept


def clamp_segments(
    highlight: HighlightResult,
    config: AppConfig,
    duration: Optional[float],
    max_segments: Optional[int] = None,
    cap: bool = True,
) -> None:
    """裁剪非法时间戳，保证在 [0, duration] 内且时长 >= min_segment_seconds。

    max_segments：截断上限（默认 config.highlight.max_segments，支持 level 策略覆盖）；
    cap=False 时跳过截断（all 档位全量保留）。
    """
    if not duration:
        return
    if max_segments is None:
        max_segments = config.highlight.max_segments
    kept = []
    for seg in highlight.segments:
        start = max(0.0, seg.start)
        end = min(duration, max(seg.end, start + config.highlight.min_segment_seconds))
        if end > start:
            kept.append(Segment(start=start, end=end, label=seg.label, confidence=seg.confidence))
    kept.sort(key=lambda s: s.start)
    if cap and len(kept) > max_segments:
        kept = [max(kept, key=lambda s: s.confidence)] + kept[:-1][: max_segments - 1]
        kept.sort(key=lambda s: s.start)
    highlight.segments = kept


def uniform_slices(duration: float, config: AppConfig, count: Optional[int] = None) -> list[Segment]:
    """把集锦目标时长均分成 N 段、均匀散布于视频全程。"""
    if count is None:
        count = max(1, config.highlight.max_segments)
    else:
        count = max(1, count)
    slice_len = max(config.highlight.min_segment_seconds, config.highlight.target_duration / count)
    slices = []
    for i in range(count):
        center = duration * (i + 0.5) / count
        start = max(0.0, center - slice_len / 2)
        end = min(duration, start + slice_len)
        if end - start < config.highlight.min_segment_seconds:
            continue
        slices.append(Segment(
            start=round(start, 2),
            end=round(end, 2),
            label="practice",
            confidence=0.9,
        ))
    return slices
