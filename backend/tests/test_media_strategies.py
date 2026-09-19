"""媒体输入策略（frame / video）单元测试。

验证：
- 注册表分发与未知模式回退 frame
- FrameStrategy 返回内容块结构（抽帧或降级文本）
- VideoStrategy 构造 video_url data URI、候选软提示文本、>128MB 仅告警不切片
"""

from __future__ import annotations

from pathlib import Path

from app.config import load_config
from app.models import Segment
from app.utils import media_strategies as ms


def test_get_strategy_dispatch_and_fallback():
    assert ms.get_strategy("frame").name == "frame"
    assert ms.get_strategy("video").name == "video"
    # 未知值回退 frame
    assert ms.get_strategy("unknown").name == "frame"
    assert ms.get_strategy(None).name == "frame"
    assert ms.get_strategy("").name == "frame"


def test_frame_strategy_returns_content_blocks():
    config = load_config()
    # 不存在的视频 → ffmpeg 抽取失败/无结果，返回降级文本块（结构稳定）
    parts = ms.FrameStrategy().build_media_parts(Path("/no/such/video.mp4"), [], config)
    assert isinstance(parts, list)
    assert all("type" in p for p in parts)


def test_video_strategy_builds_data_uri():
    config = load_config()
    p = Path(__file__).parent / "_sample_small.mp4"
    p.write_bytes(b"\x00\x01\x02smallmp4")
    try:
        parts = ms.VideoStrategy().build_media_parts(p, [], config)
    finally:
        p.unlink(missing_ok=True)
    assert parts[0]["type"] == "video_url"
    url = parts[0]["video_url"]["url"]
    assert url.startswith("data:video/mp4;base64,")


def test_video_strategy_soft_hint_with_candidates():
    config = load_config()
    p = Path(__file__).parent / "_sample_small.mp4"
    p.write_bytes(b"\x00\x01\x02smallmp4")
    cands = [Segment(start=1.0, end=5.0, label="ace", confidence=0.9)]
    try:
        parts = ms.VideoStrategy().build_media_parts(p, cands, config)
    finally:
        p.unlink(missing_ok=True)
    # 第一块是视频，第二块是候选软提示文本
    assert parts[0]["type"] == "video_url"
    assert len(parts) == 2
    assert parts[1]["type"] == "text"
    assert "参考候选" in parts[1]["text"]


def test_video_strategy_oversize_only_warns(monkeypatch):
    import app.utils.media_strategies as target

    calls = []
    monkeypatch.setattr(target.logger, "warning", lambda *a, **k: calls.append(a))

    class FakePath:
        def __init__(self, size):
            self._size = size

        def stat(self):
            class S:
                pass

            s = S()
            s.st_size = self._size
            return s

        @property
        def name(self):
            return "big.mp4"

        def read_bytes(self):
            return b"\x00\x01"

    fake = FakePath(size=200 * 1024 * 1024)  # 200MB > 128MB
    parts = target.VideoStrategy().build_media_parts(fake, [], load_config())
    # 仅告警，仍返回 video_url 块（不切片）
    assert any("超过" in str(c) for c in calls)
    assert parts[0]["type"] == "video_url"
