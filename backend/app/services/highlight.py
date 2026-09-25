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

# 后处理安全网：这些标签代表"非有效高光"（准备段/明显失误），直接丢弃
_EXCLUDED_LABELS = {"other", "out", "error", "fault", "miss", "fail"}

# 视频理解退化判定：返回"覆盖整段的单一长段"等于没剪辑，需更严格指令重试
_DEGENERATE_COVER_RATIO = 0.8

# 退化重试时追加的强制指令（要求至少切出多个段、任何单段不得覆盖过多）
_STRICT_SPLIT_APPEND = (
    "\n\n【强制补充】你上一次把整段视频返回为单一长段，这等于完全没剪辑。"
    "请严格按\"连续击球—死时间停顿—连续击球\"的节奏，把视频切成多个独立击球片段，"
    "段间的死时间（捡球 / 走位 / 等待 / 休息）务必剔除；至少返回 3 个以上 segment，"
    "且任何单个 segment 的时长不得超过视频总时长的 40%。"
)


_NO_CANDIDATES = object()  # sentinel：调用方未传 candidates 参数（legacy 管线走内部检测）


def find_highlights(
    video_path: Path,
    config: AppConfig,
    duration_seconds: Optional[float] = None,
    level: str = "intermediate",
    analysis_mode: Optional[str] = None,
    model: Optional[str] = None,
    candidates: Optional[list] = _NO_CANDIDATES,
    prompt_variant: str = "standard",
    task_id: str = "",
) -> HighlightResult:
    """识别高光回合，返回结构化时间戳。

    抽帧模式流程：ffmpeg 信号（音频击球为主 + 运动强度为辅）定位候选窗口
         → 构造领域 Prompt（携带候选时间戳与准备段排除约束）
         → 调用 LLM 段内精修 → 解析 JSON 并锚定到候选窗口（±2s）
         → 准备段后过滤（丢弃 other/低置信/与动作窗口不重叠的段）。
    视频理解流程：不跑 ffmpeg 信号检测、不依赖候选窗口（信号本就不准，故选用视频理解），
         直接传空候选，由模型观看整段视频独立定位高光；仅做时长合法性 + 准备段过滤。
    候选缺失（抽帧无信号 / 视频理解）时回退均匀切片兜底，避免整段被当成一个高光、最终只裁开头。

    level 驱动高光选择策略（config.highlight.resolve_selection）：beginner/intermediate/
    professional 各对应不同 max_segments 上限；all（所有高光回合）遍历整段、不截断候选与
    段数、置 all_highlights 标记，下游剪辑/报告据此全量拼接与分析。
    prompt_variant: Prompt 变体（standard/strict/teaching），透传至 build_highlight_prompt
    注入差异化约束（方案 12 · 阶段 1 Step 1.4）。
    """
    logger.info("highlight: start for {} (level={})", video_path.name, level)
    sel = config.highlight.resolve_selection(level)
    all_highlights = sel["all_highlights"]
    mode = analysis_mode or "frame"
    # 视频理解：ffmpeg 候选窗口本就不准（这正是选用视频理解的原因），故完全不跑信号检测、
    # 直接传空候选，由模型自由观看整段并独立定位高光；
    # 仅抽帧模式需要候选窗口锚定抽帧位置与 ±2s 精修。
    # 候选来源判断：
    # - candidates 是 _NO_CANDIDATES（未传参）→ legacy 管线，内部跑信号检测
    # - candidates 是 None（工作流节点传入 None 或显式传空）→ 无候选，不跑检测
    # - candidates 是非空列表 → 使用传入的候选
    if candidates is _NO_CANDIDATES:
        # legacy 管线：内部跑信号检测
        if mode == "frame" and duration_seconds:
            top_n = None if all_highlights else config.highlight.candidate_top_n
            _detected = event_detect.detect_candidates(
                video_path, config, float(duration_seconds), top_n=top_n
            )
            useful = bool(_detected) and not _candidates_degenerate(_detected, duration_seconds)
            _final_cands = _detected if useful else []
        else:
            useful = False
            _final_cands = []
    elif candidates:
        # 工作流上游节点传入了候选列表
        _final_cands = list(candidates)
        useful = True
    else:
        # candidates=None 或 candidates=[]：无候选，不跑检测
        useful = False
        _final_cands = []
    prompt_cands = _final_cands
    prompt = build_highlight_prompt(
        level=level, duration_seconds=duration_seconds, candidates=prompt_cands,
        select_all=all_highlights, mode=mode, prompt_variant=prompt_variant,
    )

    result = llm.complete_structured(
        video_path=video_path,
        prompt=prompt,
        config=config,
        schema_hint="HighlightResult",
        candidates=prompt_cands,
        analysis_mode=analysis_mode,
        model_override=model,
        task_id=task_id,
    )
    if result is None:
        raise RuntimeError("LLM 未返回结构化结果")

    # LLM 偶会把 scene_type/reasoning 嵌套进每个 segment 而非顶层；统一提升到顶层并兜底
    _lift_scene_fields(result)
    highlight = HighlightResult(**result)
    highlight.target_duration = config.highlight.target_duration
    highlight.all_highlights = all_highlights

    # 视频理解退化兜底：模型把整段返回为单一长段（等于没剪辑）时，用更严格指令重试一次
    if mode == "video" and _is_degenerate_single(highlight, duration_seconds):
        logger.warning(
            "highlight: 视频模式返回单一长段（覆盖整段），疑似未剪辑，改用更严格指令重试一次",
        )
        result2 = llm.complete_structured(
            video_path=video_path,
            prompt=prompt + _STRICT_SPLIT_APPEND,
            config=config,
            schema_hint="HighlightResult",
            candidates=prompt_cands,
            analysis_mode=analysis_mode,
            task_id=task_id,
        )
        if result2:
            _lift_scene_fields(result2)
            highlight = HighlightResult(**result2)
            highlight.target_duration = config.highlight.target_duration
            highlight.all_highlights = all_highlights

    if mode == "video":
        # 视频理解：模型自由定位，不锚定候选窗口；仅做时长合法性 + 准备段过滤
        _clamp_segments(
            highlight, config, duration_seconds,
            max_segments=sel["max_segments"], cap=not all_highlights,
        )
        # 准备段后过滤（仅按 label/置信度，不强制与候选重叠）
        _exclude_prep(highlight, config, [])
        # 训练/练习类若模型未产出可用高光（如整段判为 other），兜底均匀切片覆盖全程
        if not highlight.segments:
            _maybe_uniform_slices(highlight, config, duration_seconds)
    elif useful:
        # 锚定到候选窗口，避免 LLM 再次臆造时间戳
        _clamp_to_candidates(highlight, _final_cands, config, max_segments=sel["max_segments"], cap=not all_highlights)
        if not highlight.segments:
            # LLM 未返回有效高光时，退化使用候选窗口本身（仍优于全片盲剪）
            highlight.segments = _final_cands
        if all_highlights:
            # 全量模式：补齐 LLM 漏返的候选窗口，确保遍历整段、截取所有高光时刻
            _fill_missing_candidates(highlight, _final_cands, config)
        # 准备段后过滤：丢弃 other/低置信/与候选动作窗口不重叠的段
        _exclude_prep(highlight, config, _final_cands)
    else:
        _clamp_segments(highlight, config, duration_seconds, max_segments=sel["max_segments"], cap=not all_highlights)
        # 先过滤准备段/低置信段，再兜底：仅在无可用高光时才改用均匀切片。
        # 若在过滤前抢跑，会把模型返回的可用段无条件覆盖掉（训练场景 + all 档位下
        # 切片总时长只有 target_duration(15s)，全量拼接结果只有 15 秒）。
        _exclude_prep(highlight, config, [])
        if not highlight.segments:
            _maybe_uniform_slices(highlight, config, duration_seconds)
    logger.info(
        "highlight: {} segments (level={} all={} mode={})",
        len(highlight.segments), level, all_highlights, mode,
    )
    return highlight


def _is_degenerate_single(highlight: "HighlightResult", duration: Optional[float]) -> bool:
    """视频理解退化判定：仅返回 1 个 segment 且覆盖整段（≥80%），等于没剪辑。"""
    if not duration or len(highlight.segments) != 1:
        return False
    seg = highlight.segments[0]
    return (seg.end - seg.start) >= _DEGENERATE_COVER_RATIO * duration


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
    """练习/训练类视频：无可用高光时的兜底，用均匀切片覆盖全程。

    仅当 scene_type 属于非比赛类、且提供了时长时生效；比赛类（match）保留模型高光筛选。
    all 档位（highlight.all_highlights）切满全程（每段 = duration/count），
    避免兜底结果总时长被 target_duration 焊死在 15 秒。
    """
    if not duration or highlight.scene_type not in _UNIFORM_SCENE_TYPES:
        return
    full = bool(highlight.all_highlights)
    highlight.segments = _uniform_slices(duration, config, full=full)
    logger.info(
        "highlight: scene_type={} 改用均匀切片 {} 段（{}）",
        highlight.scene_type, len(highlight.segments),
        "all 档位切满全程" if full else "覆盖全程，避免仅裁开头",
    )


def _uniform_slices(duration: float, config: AppConfig, full: bool = False) -> list[Segment]:
    """把视频均分成 max_segments 段、均匀散布于全程。

    full=False（普通档位）：每段 = target_duration/count，总和约为集锦目标时长；
    full=True（all 档位）：每段 = duration/count，切满视频全程。
    """
    count = max(1, config.highlight.max_segments)
    slice_len = (
        duration / count
        if full
        else max(config.highlight.min_segment_seconds, config.highlight.target_duration / count)
    )
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


def _clamp_to_candidates(
    highlight: HighlightResult,
    candidates: list[Segment],
    config: AppConfig,
    max_segments: Optional[int] = None,
    cap: bool = True,
) -> None:
    """将 LLM 返回段锚定到候选用窗口 ±_CANDIDATE_MARGIN 内，越界拉回。

    LLM 被要求"在候选窗口 ±2s 内精修"，此处为安全网：即使模型偏离，时间戳仍由
    信号锚定的候选窗口约束，避免再次出现"永远从 0 开始"的臆造时间戳。

    max_segments：截断上限（默认 config.highlight.max_segments，支持 level 策略覆盖）；
    cap=False 时跳过截断（all 档位全量保留，仅做时间戳合法化与排序）。
    """
    if max_segments is None:
        max_segments = config.highlight.max_segments
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
    if cap and len(kept) > max_segments:
        kept = sorted(kept, key=lambda s: -s.confidence)[: max_segments]
        kept.sort(key=lambda x: x.start)
    highlight.segments = kept


def _fill_missing_candidates(highlight: HighlightResult, candidates: list[Segment], config: AppConfig) -> None:
    """all 档位兜底：把 LLM 未返回的候选窗口以候选本身补入，确保遍历整段截取所有高光。

    仅补齐与现有段不重叠（±0.5s）的候选窗口，避免重复；标签记为 candidate。
    """
    kept_spans = [(s.start, s.end) for s in highlight.segments]
    for c in candidates:
        if _overlaps_any(c, kept_spans, margin=0.5):
            continue
        highlight.segments.append(Segment(
            start=c.start, end=c.end,
            label="candidate", confidence=c.confidence,
        ))
    highlight.segments.sort(key=lambda x: x.start)


def _clamp_segments(
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


def _overlaps_any(seg: Segment, spans, margin: float = 0.0) -> bool:
    """段是否与任一候选窗口重叠（允许 margin 外扩）。"""
    for (s, e) in spans:
        if seg.start <= e + margin and seg.end >= s - margin:
            return True
    return False


def _exclude_prep(highlight: HighlightResult, config: AppConfig, candidates: list) -> None:
    """准备段后过滤安全网：丢弃 other 标签、低置信度、以及与候选动作窗口不重叠的段。

    保证 video_editor 拿到的 segments 不含捡球/走位/等待等准备段（单一真相源）。
    """
    if not config.highlight.prep_exclusion:
        return
    cfg = config.highlight
    spans = [(c.start, c.end) for c in candidates]
    kept = []
    for seg in highlight.segments:
        if seg.label in _EXCLUDED_LABELS:
            continue
        if seg.confidence < cfg.min_segment_confidence:
            continue
        if spans and not _overlaps_any(seg, spans):
            continue
        kept.append(seg)
    highlight.segments = kept


def _lift_scene_fields(result: dict) -> None:
    """LLM 偶会把 scene_type/reasoning 嵌套进每个 segment；提升到顶层并兜底推断。"""
    segs = result.get("segments") or []
    if not result.get("scene_type"):
        seg_scene = next(
            (s.get("scene_type") for s in segs if isinstance(s, dict) and s.get("scene_type")),
            None,
        )
        result["scene_type"] = seg_scene or _infer_scene_type(segs)
    if not result.get("reasoning"):
        seg_reason = next(
            (s.get("reasoning") for s in segs if isinstance(s, dict) and s.get("reasoning")),
            None,
        )
        if seg_reason:
            result["reasoning"] = seg_reason


def _infer_scene_type(segments) -> str:
    """按动作标签推断场景类型（ace/winner/smash→match；其余击球技术→practice）。"""
    labels = {s.get("label") for s in segments if isinstance(s, dict)}
    if labels & {"ace", "winner", "smash"}:
        return "match"
    if labels & {"rally", "forehand", "backhand", "serve", "volley"}:
        return "practice"
    return "unknown"


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
