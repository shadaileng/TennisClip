"""event_detect 单元测试：用 ffmpeg 合成测试片验证信号候选定位。

覆盖：
- 运动强度窗口能定位到"静态→运动"片段，且不在纯静态段产生窗口；
- 纯静态视频不产生运动窗口；
- detect_candidates 返回带真实时间戳的候选段，且覆盖真实运动段；
- 场景切换检测能定位镜头切换点。
"""

from __future__ import annotations

from pathlib import Path

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
