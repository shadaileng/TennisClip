"""高光候选定位：基于 ffmpeg 信号（场景切换 + 运动强度）定位候选时间窗口。

设计目标：把"事件发生在何时"（精确到秒）从 LLM 职责剥离，交由信号完成；
LLM 只负责"发生了什么"（ace/rally/winner/smash 语义判别）与段内边界精修。
由此从架构上消除 LLM 时间戳凭空臆造的典型问题（高光永远从视频开头排起）。

两种互补信号取并集：
- 场景切换：ffmpeg scene filter 检测镜头边界（换点/机位/回放），零成本；
- 运动强度：ffmpeg 抽灰度帧 + numpy 帧差，捕捉"稳镜头下的真实回合/ACE"。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple

from app.config import AppConfig, HighlightConfig
from app.models import Segment
from app.utils import ffmpeg
from app.utils.logger import get_logger

logger = get_logger(__name__)

try:
    import numpy as np
except ImportError:  # 允许 numpy 缺失时降级（不阻断主流程，回退方案 1）
    np = None

# 运动强度抽帧统一降到小分辨率，控制内存与算力
_MW, _MH = 160, 120
# 候选窗口相对 LLM 精修的允许外扩（秒），与 prompt 中"±2s"约束一致
_CANDIDATE_MARGIN = 2.0


def detect_scene_changes(video_path: Path, config: HighlightConfig) -> List[float]:
    """用 ffmpeg scene filter 检测镜头切换，返回切换点时间戳列表（秒）。

    showinfo 元数据打印在 stderr，故用 capture_stderr 捕获。
    """
    if not ffmpeg.is_available():
        return []
    try:
        out = ffmpeg.run(
            [
                "ffmpeg", "-i", str(video_path),
                "-vf", f"select='gt(scene,{config.scene_threshold})',showinfo",
                "-f", "null", "-",
            ],
            timeout=300,
            capture_stderr=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("event_detect: 场景切换检测失败，跳过：{}", exc)
        return []
    return [float(m.group(1)) for m in re.finditer(r"pts_time:(\d+(?:\.\d+)?)", out)]


def compute_motion_windows(
    video_path: Path, config: HighlightConfig, duration: float
) -> List[Tuple[float, float, float]]:
    """用 ffmpeg 抽灰度帧 + numpy 帧差计算运动强度，返回活跃窗口 (start, end, score)。"""
    if not ffmpeg.is_available() or np is None or duration <= 0:
        return []
    try:
        raw = ffmpeg.run(
            [
                "ffmpeg", "-y", "-i", str(video_path),
                "-vf", f"fps={config.motion_fps},format=gray,scale={_MW}:{_MH}",
                "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1",
            ],
            timeout=300,
            binary=True,
        )
        frame_bytes = _MW * _MH
        n = len(raw) // frame_bytes
        if n < 2:
            return []
        arr = np.frombuffer(raw[: n * frame_bytes], dtype=np.uint8).reshape(n, _MH, _MW)
        diffs = np.abs(arr[1:].astype(float) - arr[:-1].astype(float)).mean(axis=(1, 2))
        # 滑动均值平滑（约 1 秒窗口）
        k = max(1, int(config.motion_fps))
        if k > 1:
            diffs = np.convolve(diffs, np.ones(k) / k, mode="same")
        step = 1.0 / config.motion_fps
        active = diffs > config.motion_threshold
        return _windows_from_mask(active, step, diffs, config.min_segment_seconds)
    except Exception as exc:  # noqa: BLE001
        logger.warning("event_detect: 运动强度检测失败，跳过：{}", exc)
        return []


def _windows_from_mask(active, step, scores, min_len):
    """将逐帧活跃掩码合并为时间窗口，并向两侧各外扩 1s。"""
    windows = []
    i = 0
    n = len(active)
    while i < n:
        if active[i]:
            j = i
            while j < n and active[j]:
                j += 1
            # 活跃帧索引 [i, j) → 运动中心 (i+0.5)*step .. (j-0.5)*step
            # 仅做 ±0.5s 轻度外扩以包含动作边界；find_highlights 另有 ±2s 锚定余量
            start = max(0.0, (i + 0.5) * step - 0.5)
            end = (j - 0.5) * step + 0.5
            score = float(scores[i:j].max())
            if end - start >= min_len:
                windows.append((start, end, score))
            i = j
        else:
            i += 1
    return windows


def detect_candidates(
    video_path: Path, config: AppConfig, duration: float
) -> List[Segment]:
    """合并场景切换与运动窗口，按活跃度排序取 top-N，返回带真实时间戳的候选段。

    信号失败（ffmpeg/numpy 缺失或视频不可读）时返回空列表，由调用方回退方案 1。
    """
    if duration <= 0:
        return []
    scene_ts = detect_scene_changes(video_path, config.highlight)
    motion = compute_motion_windows(video_path, config.highlight, duration)

    candidates: List[Tuple[float, float, float]] = []
    for t in scene_ts:
        candidates.append((max(0.0, t - 1.0), t + 3.0, 1.0))
    candidates.extend(motion)

    if not candidates:
        return []

    merged = _merge_windows(candidates)
    merged.sort(key=lambda c: c[2], reverse=True)
    top = merged[: config.highlight.candidate_top_n]

    segs: List[Segment] = []
    for (s, e, _) in top:
        s = max(0.0, min(s, duration))
        e = min(duration, max(e, s + config.highlight.min_segment_seconds))
        if e - s >= config.highlight.min_segment_seconds:
            segs.append(Segment(
                start=round(s, 2), end=round(e, 2),
                label="candidate", confidence=0.9,
            ))
    segs.sort(key=lambda x: x.start)
    logger.info(
        "event_detect: {} 候选窗口（场景{} + 运动{} → 合并后{}）",
        len(segs), len(scene_ts), len(motion), len(merged),
    )
    return segs


def _merge_windows(candidates):
    """合并重叠/邻近窗口，取每段的峰值活跃度。"""
    cand = sorted(candidates, key=lambda c: c[0])
    merged = [list(cand[0])]
    for s, e, sc in cand[1:]:
        last = merged[-1]
        if s <= last[1] + 0.5:
            last[1] = max(last[1], e)
            last[2] = max(last[2], sc)
        else:
            merged.append([s, e, sc])
    return [tuple(m) for m in merged]
