"""高光候选定位：基于 ffmpeg 信号（场景切换 + 运动强度）定位候选时间窗口。

设计目标：把"事件发生在何时"（精确到秒）从 LLM 职责剥离，交由信号完成；
LLM 只负责"发生了什么"（ace/rally/winner/smash 语义判别）与段内边界精修。
由此从架构上消除 LLM 时间戳凭空臆造的典型问题（高光永远从视频开头排起）。

可插拔信号管线（按 candidate_mode 选择主信号与回退链），默认 audio_motion：
- 音频击球事件：ffmpeg 抽裸 PCM → 瞬态包络 → numpy 峰值检测球拍触球瞬态（"砰"声），
  只出现在真实挥拍/相持，捡球/走位/等待完全无声，从根上排除准备段；
- 运动强度：ffmpeg 抽灰度帧 + numpy 帧差，自适应分位阈值捕捉"稳镜头下的真实回合/ACE"；
- 场景切换：ffmpeg scene filter 检测镜头边界（换点/机位/回放），作为可选信号。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

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


def _per_frame_motion(video_path: Path, config: HighlightConfig, duration: float):
    """抽取逐帧运动分数（灰度帧差均值），返回 (scores, step)；失败返回 None。

    供 compute_motion_windows 与 compute_action_bursts 复用，避免重复抽帧。
    """
    if not ffmpeg.is_available() or np is None or duration <= 0:
        return None
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
            return None
        arr = np.frombuffer(raw[: n * frame_bytes], dtype=np.uint8).reshape(n, _MH, _MW)
        diffs = np.abs(arr[1:].astype(float) - arr[:-1].astype(float)).mean(axis=(1, 2))
        # 滑动均值平滑（约 1 秒窗口）
        k = max(1, int(config.motion_fps))
        if k > 1:
            diffs = np.convolve(diffs, np.ones(k) / k, mode="same")
        step = 1.0 / config.motion_fps
        return diffs, step
    except Exception as exc:  # noqa: BLE001
        logger.warning("event_detect: 运动强度抽帧失败，跳过：{}", exc)
        return None


def compute_motion_windows(
    video_path: Path, config: HighlightConfig, duration: float
) -> List[Tuple[float, float, float]]:
    """用 ffmpeg 抽灰度帧 + numpy 帧差计算运动强度，返回活跃窗口 (start, end, score)。

    使用单一绝对阈值 motion_threshold（保留旧行为，并作为动作爆发的下界）。
    """
    res = _per_frame_motion(video_path, config, duration)
    if res is None:
        return []
    scores, step = res
    if len(scores) < 2:
        return []
    active = scores > config.motion_threshold
    return _windows_from_mask(active, step, scores, config.min_segment_seconds)


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


def _extract_audio_samples(video_path: Path, config: HighlightConfig):
    """用 ffmpeg 抽单声道 s16le 裸 PCM；无音轨/失败返回 None（安全降级）。"""
    if not ffmpeg.is_available():
        return None
    try:
        raw = ffmpeg.run(
            [
                "ffmpeg", "-y", "-i", str(video_path),
                "-vn", "-ac", "1", "-ar", str(config.audio_sample_rate),
                "-f", "s16le", "pipe:1",
            ],
            timeout=300,
            binary=True,
        )
        if not raw:
            return None
        return np.frombuffer(raw, dtype=np.int16)
    except Exception as exc:  # noqa: BLE001
        logger.warning("event_detect: 音频抽取失败，跳过音频信号：{}", exc)
        return None


def _detect_hits(samples, sr: int, config: HighlightConfig) -> List[float]:
    """检测球拍击球瞬态（"砰"声）：瞬态包络 + numpy 本地峰值，返回击球时间戳（秒）。

    击球瞬态只出现在真实挥拍/相持，捡球/走位/等待无声 → 自然不入候选。
    """
    if samples is None or len(samples) < sr // 10:
        return []
    x = samples.astype(np.float32)
    # 一阶差分绝对值能量对击球"咔"声最敏感
    env = np.abs(np.diff(x))
    # 分帧（~20ms）求均值，得到瞬态包络
    win = max(1, sr // 50)
    n = len(env) // win
    framed = env[: n * win].reshape(n, win).mean(axis=1) if n >= 2 else env
    if framed.size == 0:
        return []
    thr = max(1e-6, float(np.percentile(framed, config.audio_hit_percentile)))
    # 本地峰值：严格大于等于两侧邻居且超过阈值（无 scipy，自实现）
    above = framed > thr
    peak_idx = np.where(
        above[1:-1] & (framed[1:-1] >= framed[:-2]) & (framed[1:-1] >= framed[2:])
    )[0] + 1
    # 最小峰间距去抖（约 80ms）
    hits = []
    last = -10 ** 9
    for idx in peak_idx:
        t = (idx + 0.5) * win / sr
        if t - last >= 0.08:
            hits.append(float(t))
            last = t
    return hits


def _cluster_hits(hits: List[float], config: HighlightConfig, duration: float):
    """将击球时间戳按间隔聚类为回合窗口 (start, end, score=击球数)。"""
    if not hits:
        return []
    gap = config.hit_cluster_gap_seconds
    clusters: List[List[float]] = [[hits[0]]]
    for h in hits[1:]:
        if h - clusters[-1][-1] <= gap:
            clusters[-1].append(h)
        else:
            clusters.append([h])
    out = []
    for c in clusters:
        start = max(0.0, c[0] - config.hit_window_expand)
        end = min(duration, c[-1] + config.hit_window_expand)
        out.append((start, end, float(len(c))))
    return out


def compute_action_bursts(
    video_path: Path, config: HighlightConfig, duration: float
) -> List[Tuple[float, float, float]]:
    """运动强度分位爆发：自适应阈值（分位 + 绝对下界）+ 持续高强度约束，返回爆发窗口。

    用于把音频击球窗口扩展到完整挥拍，并在无声视频时兜底。
    """
    res = _per_frame_motion(video_path, config, duration)
    if res is None:
        return []
    scores, step = res
    if len(scores) < 2:
        return []
    thr = max(
        config.motion_threshold,
        float(np.percentile(scores, config.motion_action_percentile)),
    )
    active = scores >= thr
    return _windows_from_mask(active, step, scores, config.min_segment_seconds)


def _audio_candidates(video_path, cfg, duration):
    if not cfg.audio_enabled:
        return []
    samples = _extract_audio_samples(video_path, cfg)
    if samples is None:
        return []
    hits = _detect_hits(samples, cfg.audio_sample_rate, cfg)
    if not hits:
        return []
    return _cluster_hits(hits, cfg, duration)


def _motion_candidates(video_path, cfg, duration):
    return compute_action_bursts(video_path, cfg, duration)


def _scene_candidates(video_path, cfg, duration):
    ts = detect_scene_changes(video_path, cfg)
    return [(max(0.0, t - 1.0), t + 3.0, 1.0) for t in ts]


_STRATEGY_ORDER = {
    "audio": ["audio"],
    "motion": ["motion"],
    "scene": ["scene"],
    "audio_motion": ["audio", "motion"],
    "auto": ["audio", "motion", "scene"],
}
_STRATEGIES = {
    "audio": _audio_candidates,
    "motion": _motion_candidates,
    "scene": _scene_candidates,
}


def _enrich_with_motion(audio_windows, motion_windows, cfg):
    """用邻近运动爆发扩展音频窗口，使其覆盖完整挥拍（含预备/随挥）。"""
    out = []
    for (s, e, sc) in audio_windows:
        best = [s, e, sc]
        for (ms, me, _) in motion_windows:
            if ms <= e + cfg.hit_window_expand and me >= s - cfg.hit_window_expand:
                best[0] = min(best[0], ms)
                best[1] = max(best[1], me)
                best[2] = max(best[2], sc)
        out.append(tuple(best))
    return out


def detect_candidates(
    video_path: Path, config: AppConfig, duration: float, top_n: Optional[int] = None
) -> List[Segment]:
    """可插拔候选定位：按 candidate_mode 选择主信号与回退链，返回带真实时间戳的候选段。

    默认 audio_motion（音频击球为主、运动爆发辅助/兜底）；音频缺失/无声时自动回退运动；
    两者皆空返回 []，交由 highlight 回退均匀切片。信号失败（ffmpeg/numpy 缺失或视频不可读）
    时返回空列表。

    top_n：合并后候选窗口保留上限；为 None（默认）时沿用 cfg.candidate_top_n。
    传 None 显式放开上限（如 all 档位需要遍历整段全部候选）。
    """
    if duration <= 0:
        return []
    cfg = config.highlight
    mode = (cfg.candidate_mode or "audio_motion").lower()
    order = _STRATEGY_ORDER.get(mode, _STRATEGY_ORDER["audio_motion"])
    candidates = []
    source = None
    for name in order:
        cands = _STRATEGIES[name](video_path, cfg, duration)
        if cands:
            candidates = cands
            source = name
            break
    # audio_motion：用运动爆发扩展音频窗口到完整挥拍
    if mode == "audio_motion" and source == "audio":
        motion = _STRATEGIES["motion"](video_path, cfg, duration)
        if motion:
            candidates = _enrich_with_motion(candidates, motion, cfg)
    if not candidates:
        logger.info("event_detect: 无信号候选（mode={}），交由 highlight 回退均匀切片", mode)
        return []
    merged = _merge_windows(candidates)
    merged.sort(key=lambda c: c[2], reverse=True)
    # top_n=None 表示放开上限（保留全部合并候选）；正整数则按上限截断。
    top = merged if top_n is None else merged[: top_n]
    segs: List[Segment] = []
    for (s, e, _) in top:
        s = max(0.0, min(s, duration))
        e = min(duration, max(e, s + cfg.min_segment_seconds))
        if e - s >= cfg.min_segment_seconds:
            segs.append(Segment(
                start=round(s, 2), end=round(e, 2),
                label="candidate", confidence=0.9,
            ))
    segs.sort(key=lambda x: x.start)
    logger.info(
        "event_detect: {} 候选窗口（mode={} 来源={} → 合并后{}）",
        len(segs), mode, source, len(merged),
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
