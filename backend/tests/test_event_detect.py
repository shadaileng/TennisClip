"""event_detect 单元测试：用 ffmpeg 合成测试片验证信号候选定位。

覆盖：
- 运动强度窗口能定位到"静态→运动"片段，且不在纯静态段产生窗口；
- 纯静态视频不产生运动窗口；
- detect_candidates 返回带真实时间戳的候选段，且覆盖真实运动段；
- 场景切换检测能定位镜头切换点。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.config import load_config
from app.services import event_detect
from app.utils import ffmpeg

SKIP = not ffmpeg.is_available()


def _make_video(path: Path, kind: str) -> None:
    if kind == "static_then_moving":
        ffmpeg.run([
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=160x120:d=2",
            "-f", "lavfi", "-i", "testsrc=s=160x120:d=2",
            "-filter_complex", "[0][1]concat=v=1:a=0",
            str(path),
        ])
    elif kind == "static":
        ffmpeg.run([
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=160x120:d=3",
            str(path),
        ])
    elif kind == "scene":
        ffmpeg.run([
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=red:s=160x120:d=2",
            "-f", "lavfi", "-i", "color=c=blue:s=160x120:d=2",
            "-filter_complex", "[0][1]concat=v=1:a=0",
            str(path),
        ])
    else:
        raise ValueError(kind)


def _overlaps_any(windows, a, b):
    return any(not (w[1] <= a or w[0] >= b) for w in windows)


@pytest.mark.skipif(SKIP, reason="ffmpeg 不可用")
def test_motion_windows_localized(tmp_path):
    cfg = load_config()
    video = tmp_path / "v.mp4"
    _make_video(video, "static_then_moving")
    windows = event_detect.compute_motion_windows(video, cfg.highlight, 4.0)
    assert windows, "应检测到运动窗口"
    # 运动发生在 [2,4]，静态 [0,2] 不应有窗口
    assert _overlaps_any(windows, 1.8, 4.0), "运动窗口应覆盖 [2,4]"
    assert not _overlaps_any(windows, 0.0, 0.8), "静态段不应产生运动窗口"


@pytest.mark.skipif(SKIP, reason="ffmpeg 不可用")
def test_static_video_no_motion(tmp_path):
    cfg = load_config()
    video = tmp_path / "s.mp4"
    _make_video(video, "static")
    windows = event_detect.compute_motion_windows(video, cfg.highlight, 3.0)
    assert not _overlaps_any(windows, 0.0, 2.8), "纯静态视频不应有运动窗口"


@pytest.mark.skipif(SKIP, reason="ffmpeg 不可用")
def test_detect_candidates_overlaps_motion(tmp_path):
    cfg = load_config()
    cfg.highlight.min_segment_seconds = 1  # 移动段仅 ~2s，调小最短时长以便检测
    video = tmp_path / "v.mp4"
    _make_video(video, "static_then_moving")
    cands = event_detect.detect_candidates(video, cfg, 4.0)
    assert cands, "应返回候选窗口"
    spans = [(c.start, c.end) for c in cands]
    assert _overlaps_any(spans, 1.8, 4.0), "候选窗口应覆盖真实运动段 [2,4]"


@pytest.mark.skipif(SKIP, reason="ffmpeg 不可用")
def test_detect_candidates_static_empty(tmp_path):
    cfg = load_config()
    video = tmp_path / "s.mp4"
    _make_video(video, "static")
    cands = event_detect.detect_candidates(video, cfg, 3.0)
    assert cands == [], "纯静态视频应无候选窗口"


@pytest.mark.skipif(SKIP, reason="ffmpeg 不可用")
def test_scene_change_detected(tmp_path):
    cfg = load_config()
    video = tmp_path / "sc.mp4"
    _make_video(video, "scene")
    ts = event_detect.detect_scene_changes(video, cfg.highlight)
    assert any(1.5 <= t <= 2.5 for t in ts), f"应检测到 ~2s 处的场景切换，实际 {ts}"


def _make_samples_with_hits(sr, hit_times, duration=12.0, hit_len=0.05):
    """生成带击球瞬态（随机高幅脉冲）的 PCM，用于音频检测单测。"""
    rng = np.random.RandomState(0)
    x = np.zeros(int(sr * duration), dtype=np.int16)
    for t in hit_times:
        i = int(t * sr)
        n = max(1, int(hit_len * sr))
        x[i:i + n] = (rng.rand(n) * 20000 - 10000).astype(np.int16)
    return x


@pytest.mark.skipif(SKIP, reason="ffmpeg 不可用")
def test_detect_hits_finds_impulses():
    cfg = load_config()
    sr = cfg.highlight.audio_sample_rate
    hits = [2.0, 5.0, 9.0]
    samples = _make_samples_with_hits(sr, hits)
    found = event_detect._detect_hits(samples, sr, cfg.highlight)
    assert found, "应检测到击球瞬态"
    for h in hits:
        assert any(abs(f - h) <= 0.3 for f in found), f"未命中击球点 {h}，实际 {found}"


@pytest.mark.skipif(SKIP, reason="ffmpeg 不可用")
def test_detect_hits_silent_empty():
    cfg = load_config()
    sr = cfg.highlight.audio_sample_rate
    samples = np.zeros(sr * 5, dtype=np.int16)
    assert event_detect._detect_hits(samples, sr, cfg.highlight) == []


@pytest.mark.skipif(SKIP, reason="ffmpeg 不可用")
def test_cluster_hits_groups_by_gap():
    cfg = load_config()
    hits = [1.0, 2.0, 3.0, 20.0, 21.0]
    clusters = event_detect._cluster_hits(hits, cfg.highlight, 30.0)
    assert len(clusters) == 2
    assert clusters[0][0] <= 1.0 and clusters[0][1] >= 3.0
    # 第二段起始受 hit_window_expand(2s) 外扩影响，应在 20 附近（含外扩）
    assert clusters[1][0] <= 20.0 and clusters[1][1] >= 21.0


@pytest.mark.skipif(SKIP, reason="ffmpeg 不可用")
def test_cluster_hits_empty():
    cfg = load_config()
    assert event_detect._cluster_hits([], cfg.highlight, 30.0) == []


@pytest.mark.skipif(SKIP, reason="ffmpeg 不可用")
def test_candidate_mode_audio_motion_fallback(tmp_path, monkeypatch):
    cfg = load_config()
    # 音频策略返回空 → 应回退到 motion 策略的占位窗口（打补丁注册表，因 detect_candidates 走 _STRATEGIES）
    monkeypatch.setattr(
        event_detect, "_STRATEGIES",
        {"audio": lambda *a, **k: [], "motion": lambda *a, **k: [(1.0, 5.0, 1.0)]},
    )
    cands = event_detect.detect_candidates(tmp_path / "x.mp4", cfg, 30.0)
    assert cands, "音频空时应回退运动候选"


@pytest.mark.skipif(SKIP, reason="ffmpeg 不可用")
def test_detect_candidates_top_n_none_returns_all(tmp_path, monkeypatch):
    """top_n=None 放开上限保留全部合并候选；缺省 top_n 沿用 candidate_top_n 截断。"""
    cfg = load_config()
    cfg.highlight.candidate_top_n = 2  # 收紧默认上限以区分两种行为
    many = [(1.0, 5.0, 1.0), (6.0, 10.0, 1.0), (11.0, 15.0, 1.0), (16.0, 20.0, 1.0)]
    monkeypatch.setattr(
        event_detect, "_STRATEGIES",
        {"audio": lambda *a, **k: many, "motion": lambda *a, **k: [], "scene": lambda *a, **k: []},
    )
    cands_all = event_detect.detect_candidates(tmp_path / "x.mp4", cfg, 30.0, top_n=None)
    assert len(cands_all) == 4, "top_n=None 应保留全部合并候选"
    # 显式 top_n=2 应按上限截断；缺省 top_n 现在即不截断（由调用方 find_highlights 显式传 candidate_top_n）
    cands_capped = event_detect.detect_candidates(tmp_path / "x.mp4", cfg, 30.0, top_n=2)
    assert len(cands_capped) == 2, "显式 top_n 应按上限截断"
    cands_default = event_detect.detect_candidates(tmp_path / "x.mp4", cfg, 30.0)
    assert len(cands_default) == 4, "缺省 top_n 不截断（与 all 档位一致）"
