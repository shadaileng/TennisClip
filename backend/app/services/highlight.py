"""高光识别：调用 Step 3.7 Flash 多模态理解，输出结构化时间戳。"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Optional

from app.config import AppConfig
from app.models import HighlightResult, Segment
from app.services import event_detect
from app.utils import llm
from app.utils.logger import get_logger

from prompts.highlight_analysis import build_highlight_prompt

logger = get_logger(__name__)

# 这些场景类型下，模型往往把整段判为单个 "other"，高光筛选无意义；
# 改用均匀切片，让集锦覆盖全程而非仅开头（仅在无信号候选时启用）。
_UNIFORM_SCENE_TYPES = {"training", "practice", "unknown"}

# 候选窗口相对 LLM 精修的允许外扩（秒），与 prompt 中"±2s"约束一致
_CANDIDATE_MARGIN = 2.0


def find_highlights(
    video_path: Path,
    config: AppConfig,
    duration_seconds: Optional[float] = None,
    level: str = "intermediate",
) -> HighlightResult:
    """识别高光回合，返回结构化时间戳。

    流程：ffmpeg 信号定位候选窗口 → 构造领域 Prompt（携带候选时间戳）
         → 调用 LLM（自动 Mock 回退）段内精修 → 解析 JSON 并锚定到候选窗口。
    候选窗口若退化为"覆盖几乎整段"（如单人练球全程连续运动，信号未能真正定位到
    局部事件），视为无定位价值，回退到旧逻辑（全片带时间戳采样 + 均匀切片兜底），
    避免整段被当成一个高光、最终只裁开头。
    """
    logger.info("highlight: start for {}", video_path.name)
    candidates = (
        event_detect.detect_candidates(video_path, config, float(duration_seconds))
        if duration_seconds else []
    )
    useful = bool(candidates) and not _candidates_degenerate(candidates, duration_seconds)
    prompt_cands = candidates if useful else []
    prompt = build_highlight_prompt(
        level=level, duration_seconds=duration_seconds, candidates=prompt_cands
    )

    result = llm.complete_structured(
        video_path=video_path,
        prompt=prompt,
        config=config,
        schema_hint="HighlightResult",
        candidates=prompt_cands,
    )
    if result is None:
        raise RuntimeError("LLM 未返回结构化结果")

    highlight = HighlightResult(**result)
    highlight.target_duration = config.highlight.target_duration
    if useful:
        # 锚定到候选窗口，避免 LLM 再次臆造时间戳
        _clamp_to_candidates(highlight, candidates, config)
        if not highlight.segments:
            # LLM 未返回有效高光时，退化使用候选窗口本身（仍优于全片盲剪）
            highlight.segments = candidates
    else:
        _clamp_segments(highlight, config, duration_seconds)
        _maybe_uniform_slices(highlight, config, duration_seconds)
    logger.info("highlight: {} segments (scene_type={})", len(highlight.segments), highlight.scene_type)
    return highlight


def _candidates_degenerate(candidates: list[Segment], duration: Optional[float]) -> bool:
    """候选窗口是否退化为"覆盖几乎整段"，即信号未能真正定位到局部事件。

    典型场景：单人练球/对墙练习，全程连续运动，运动强度窗口铺满整段。
    此时候选失去定位意义，应回退到均匀切片等兜底逻辑。判定：恰好 1 个候选、
    且覆盖时长 ≥ 视频总长的 80%。
    """
    if not candidates or len(candidates) != 1 or not duration:
        return False
    cov = candidates[0].end - candidates[0].start
    return cov >= 0.8 * duration


def _maybe_uniform_slices(
    highlight: HighlightResult, config: AppConfig, duration: Optional[float]
) -> None:
    """练习/训练类视频：用均匀切片替换模型整段 other，使集锦覆盖全程。

    仅当 scene_type 属于非比赛类、且提供了时长时生效；比赛类（match）保留模型高光筛选。
    """
    if not duration or highlight.scene_type not in _UNIFORM_SCENE_TYPES:
        return
    highlight.segments = _uniform_slices(duration, config)
    logger.info(
        "highlight: scene_type={} 改用均匀切片 {} 段（覆盖全程，避免仅裁开头）",
        highlight.scene_type, len(highlight.segments),
    )


def _uniform_slices(duration: float, config: AppConfig) -> list[Segment]:
    """把集锦目标时长均分成 max_segments 段、均匀散布于视频全程。"""
    count = max(1, config.highlight.max_segments)
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


def _clamp_to_candidates(highlight: HighlightResult, candidates: list[Segment], config: AppConfig) -> None:
    """将 LLM 返回段锚定到候选用窗口 ±_CANDIDATE_MARGIN 内，越界拉回。

    LLM 被要求"在候选窗口 ±2s 内精修"，此处为安全网：即使模型偏离，时间戳仍由
    信号锚定的候选窗口约束，避免再次出现"永远从 0 开始"的臆造时间戳。
    """
    spans = [(c.start, c.end) for c in candidates]
    kept = []
    for seg in highlight.segments:
        mid = (seg.start + seg.end) / 2
        best = min(spans, key=lambda sp: abs((sp[0] + sp[1]) / 2 - mid))
        lo = best[0] - _CANDIDATE_MARGIN
        hi = best[1] + _CANDIDATE_MARGIN
        s = max(lo, min(seg.start, hi))
        e = max(s + config.highlight.min_segment_seconds * 0.5, min(seg.end, hi))
        if e - s >= config.highlight.min_segment_seconds * 0.5:
            kept.append(Segment(
                start=round(s, 2), end=round(e, 2),
                label=seg.label, confidence=seg.confidence,
            ))
    kept.sort(key=lambda x: x.start)
    if len(kept) > config.highlight.max_segments:
        kept = sorted(kept, key=lambda s: -s.confidence)[: config.highlight.max_segments]
        kept.sort(key=lambda x: x.start)
    highlight.segments = kept


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
