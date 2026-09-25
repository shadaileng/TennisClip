"""Step 2 测试：节点实现（薄封装既有 services）。

TC-06 ~ TC-14：各节点的输入输出、参数透传、必填校验、行为一致性。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pytest

from app.config import AppConfig
from app.models import HighlightResult, Segment, TechnicalReport
from app.workflow.spec import get_fn, get_spec

# 确保所有内置节点已注册（@register 装饰器在模块加载时触发）
import app.workflow.nodes  # noqa: F401


# ---------- 测试用 Context ----------

@dataclass
class FakeContext:
    """模拟执行器 Context，供节点函数调用。"""
    video_path: Path = field(default_factory=lambda: Path("/tmp/test.mp4"))
    config: AppConfig = field(default_factory=lambda: AppConfig())
    task_out: Path = field(default_factory=lambda: Path("/tmp/out"))
    task_id: str = "test_task_01"
    level: str = "intermediate"
    inputs: dict = field(default_factory=dict)
    result: object = None


# ---------- TC-06: input.video 输出 video 端口 ----------

def test_input_video_outputs_source_path():
    """input.video 输出 video 端口 = 执行器注入的源视频路径。"""
    fn = get_fn("input.video")
    assert fn is not None
    ctx = FakeContext(video_path=Path("/data/sample.mp4"))
    result = fn(ctx, {})
    assert result == {"video": Path("/data/sample.mp4")}


# ---------- TC-07: preprocess.transcode 调用 preprocess ----------

def test_preprocess_transcode_calls_preprocess(tmp_path, monkeypatch):
    """preprocess.transcode 调用 preprocess.preprocess（monkeypatch 桩）并输出 video + duration。"""
    from app.workflow.nodes import preprocess_transcode

    called = {}

    def fake_preprocess(video_path, config, work_dir=None):
        called["video"] = video_path
        called["config"] = config
        return tmp_path / "processed.mp4"

    def fake_probe(path):
        return {"duration": 120.5, "width": 1280, "height": 720, "fps": 30.0}

    from app.services import preprocess as pre_svc
    monkeypatch.setattr(pre_svc, "preprocess", fake_preprocess)
    monkeypatch.setattr(pre_svc, "probe_video", fake_probe)
    # stub db_service
    from app.services import db_service
    monkeypatch.setattr(db_service, "record_task_input", lambda **kw: None)

    ctx = FakeContext(video_path=tmp_path / "input.mp4", task_out=tmp_path)
    result = preprocess_transcode.run(ctx, {"height": 720, "fps": 30, "max_input_seconds": 300})

    assert result["video"] == tmp_path / "processed.mp4"
    assert result["duration"] == 120.5
    assert called["video"] == tmp_path / "input.mp4"
    # 分辨率/帧率透传校验：height=720 → resolution="720p"，fps=30
    assert called["config"].video.resolution == "720p"
    assert called["config"].video.fps == 30


def test_preprocess_transcode_height_360p(tmp_path, monkeypatch):
    """n2 高度参数可配：height=360 时 config.video.resolution="360p"（CV 增强流降本场景）。"""
    from app.workflow.nodes import preprocess_transcode

    called = {}

    def fake_preprocess(video_path, config, work_dir=None):
        called["video"] = video_path
        called["config"] = config
        return tmp_path / "processed.mp4"

    def fake_probe(path):
        return {"duration": 120.5, "width": 640, "height": 360, "fps": 30.0}

    from app.services import preprocess as pre_svc
    monkeypatch.setattr(pre_svc, "preprocess", fake_preprocess)
    monkeypatch.setattr(pre_svc, "probe_video", fake_probe)
    from app.services import db_service
    monkeypatch.setattr(db_service, "record_task_input", lambda **kw: None)

    ctx = FakeContext(video_path=tmp_path / "input.mp4", task_out=tmp_path)
    preprocess_transcode.run(ctx, {"height": 360, "fps": 30, "max_input_seconds": 300})
    assert called["config"].video.resolution == "360p"
    assert called["config"].video.resolution_height == 360
    assert called["config"].video.fps == 30


# ---------- TC-08: detect.candidates 透传 mode/top_n ----------

def test_detect_candidates透传_params(tmp_path, monkeypatch):
    """detect.candidates 的 mode / top_n 透传至 event_detect.detect_candidates。"""
    from app.workflow.nodes import detect_candidates

    captured = {}

    def fake_detect(video, config, duration, top_n=None):
        captured["video"] = video
        captured["duration"] = duration
        captured["top_n"] = top_n
        captured["mode"] = config.highlight.candidate_mode
        return []

    from app.services import event_detect
    monkeypatch.setattr(event_detect, "detect_candidates", fake_detect)

    config = AppConfig()
    ctx = FakeContext(
        video_path=tmp_path / "v.mp4",
        config=config,
        inputs={"video": tmp_path / "v.mp4", "duration": 60.0},
    )
    result = detect_candidates.run(ctx, {"mode": "audio", "top_n": 3})

    assert captured["top_n"] == 3
    assert captured["mode"] == "audio"
    assert captured["duration"] == 60.0


def test_detect_candidates_top_n_zero放开上限(tmp_path, monkeypatch):
    """detect.candidates top_n=0 传 None 表示放开上限。"""
    from app.workflow.nodes import detect_candidates

    captured = {}

    def fake_detect(video, config, duration, top_n=None):
        captured["top_n"] = top_n
        return []

    from app.services import event_detect
    monkeypatch.setattr(event_detect, "detect_candidates", fake_detect)

    ctx = FakeContext(
        video_path=tmp_path / "v.mp4",
        inputs={"video": tmp_path / "v.mp4", "duration": 60.0},
    )
    detect_candidates.run(ctx, {"mode": "audio_motion", "top_n": 0})
    assert captured["top_n"] is None  # None = 放开上限


# ---------- TC-09: analyze.highlight 透传 level/analysis_mode ----------

def test_analyze_highlight透传_params(tmp_path, monkeypatch):
    """analyze.highlight 的 level / analysis_mode 透传至 highlight.find_highlights。"""
    from app.workflow.nodes import analyze_highlight

    captured = {}

    def fake_highlights(video, config, duration, level="intermediate", analysis_mode=None,
                        model=None, candidates=None, prompt_variant="standard", task_id=""):
        captured["level"] = level
        captured["analysis_mode"] = analysis_mode
        captured["model"] = model
        captured["candidates"] = candidates
        captured["prompt_variant"] = prompt_variant
        return HighlightResult(segments=[], scene_type="training")

    from app.services import highlight as hl_svc
    monkeypatch.setattr(hl_svc, "find_highlights", fake_highlights)

    config = AppConfig()
    ctx = FakeContext(
        video_path=tmp_path / "v.mp4",
        config=config,
        inputs={"video": tmp_path / "v.mp4", "duration": 90.0},
    )
    result = analyze_highlight.run(ctx, {"level": "professional", "analysis_mode": "video"})

    assert captured["level"] == "professional"
    assert captured["analysis_mode"] == "video"
    assert captured["prompt_variant"] == "standard"  # 缺省参数回落 standard
    assert result["highlight"].scene_type == "training"


# ---------- TC-10: post.filter_segments 剔除低置信度与黑名单标签 ----------

def test_post_filter_excludes_other_and_low_conf():
    """post.filter_segments 剔除 other 标签和低置信度段。"""
    from app.workflow.nodes import post_filter

    ctx = FakeContext(
        inputs={
            "highlight": HighlightResult(
                segments=[
                    Segment(start=1, end=5, label="ace", confidence=0.9),
                    Segment(start=6, end=10, label="other", confidence=0.8),
                    Segment(start=11, end=15, label="rally", confidence=0.3),
                    Segment(start=16, end=20, label="winner", confidence=0.95),
                ]
            )
        }
    )
    result = post_filter.run(ctx, {
        "min_confidence": 0.6,
        "exclude_labels": "other,out,error",
        "min_duration": 0.0,
    })

    labels = {s.label for s in result["highlight"].segments}
    assert "other" not in labels
    assert "rally" not in labels  # confidence=0.3 < 0.6
    assert labels == {"ace", "winner"}


# ---------- TC-11: post.uniform_slices 产出 N 段覆盖全程 ----------

def test_post_uniform_slices_count_and_coverage():
    """post.uniform_slices 按 count 产出 N 段且总时长覆盖全程。"""
    from app.workflow.nodes import post_uniform

    config = AppConfig()
    ctx = FakeContext(config=config, inputs={"duration": 120.0})
    result = post_uniform.run(ctx, {"count": 4, "slice_seconds": 5.0})

    hl = result["highlight"]
    assert len(hl.segments) == 4
    # 片段散布于全程：120s / 4 = 30s 每区，第一段中心 15，末段中心 105
    assert hl.segments[0].start < 15
    assert hl.segments[-1].end > 100


# ---------- TC-12: edit.concat all_mode 忽略 target_duration ----------

def test_edit_concat_all_mode(tmp_path, monkeypatch):
    """edit.concat 在 all_mode=true 时忽略 target_duration。"""
    from app.workflow.nodes import edit_concat

    called = {}

    def fake_edit(source, highlight, config):
        called["all"] = highlight.all_highlights
        called["segments"] = len(highlight.segments)
        out = tmp_path / "highlight_all.mp4"
        out.write_bytes(b"dummy")
        return out

    from app.services import video_editor as editor_svc
    monkeypatch.setattr(editor_svc, "edit_highlight_video", fake_edit)
    from app.services import db_service
    monkeypatch.setattr(db_service, "record_task_output", lambda *a, **kw: None)

    hl = HighlightResult(
        segments=[
            Segment(start=0, end=10, label="ace", confidence=0.9),
            Segment(start=20, end=30, label="winner", confidence=0.85),
        ]
    )
    ctx = FakeContext(video_path=tmp_path / "source.mp4", task_out=tmp_path)
    ctx.inputs = {"video": tmp_path / "source.mp4", "highlight": hl}

    result = edit_concat.run(ctx, {"target_duration": 15, "all_mode": True})

    assert called["all"] is True
    assert result["video"] == tmp_path / "highlight_all.mp4"


# ---------- TC-13: report.technical optional=True, expensive=True ----------

def test_report_technical_optional_and_expensive():
    """report.technical 的 optional=True、expensive 标记存在。"""
    spec = get_spec("report.technical")
    assert spec is not None
    assert spec.optional is True
    assert spec.expensive is True


def test_report_technical透传_level(tmp_path, monkeypatch):
    """report.technical 的 level 透传至 report.generate_report。"""
    from app.workflow.nodes import report_technical

    captured = {}

    def fake_report(video, highlight, config, level="intermediate", out_path=None,
                    knowledge_level="standard", task_id=""):
        captured["level"] = level
        captured["knowledge_level"] = knowledge_level
        return TechnicalReport(level=level, summary="test")

    from app.services import report as report_svc
    monkeypatch.setattr(report_svc, "generate_report", fake_report)
    from app.services import db_service
    monkeypatch.setattr(db_service, "record_task_output", lambda *a, **kw: None)

    ctx = FakeContext(
        video_path=tmp_path / "v.mp4",
        task_out=tmp_path,
        inputs={
            "video": tmp_path / "v.mp4",
            "highlight": HighlightResult(segments=[], scene_type="match"),
        },
    )
    result = report_technical.run(ctx, {"level": "professional"})

    assert captured["level"] == "professional"
    assert captured["knowledge_level"] == "standard"  # 缺省参数回落 standard
    assert result["report"].level == "professional"


# ---------- TC-14: 缺失必填输入时抛清晰 ValueError ----------

def test_analyze_highlight_missing_video():
    """缺失必填输入 video 时抛清晰 ValueError（含节点 type 与端口名）。"""
    from app.workflow.nodes import analyze_highlight

    ctx = FakeContext(inputs={"duration": 60.0})
    with pytest.raises(ValueError, match="analyze.highlight.*video"):
        analyze_highlight.run(ctx, {"level": "intermediate", "analysis_mode": "frame"})


def test_edit_concat_missing_highlight():
    """缺失必填输入 highlight 时抛清晰 ValueError。"""
    from app.workflow.nodes import edit_concat

    ctx = FakeContext(inputs={"video": Path("/tmp/v.mp4")})
    with pytest.raises(ValueError, match="edit.concat.*highlight"):
        edit_concat.run(ctx, {"target_duration": 15, "all_mode": False})


def test_post_uniform_missing_duration():
    """缺失必填输入 duration 时抛清晰 ValueError。"""
    from app.workflow.nodes import post_uniform

    ctx = FakeContext(inputs={})
    with pytest.raises(ValueError, match="post.uniform_slices.*duration"):
        post_uniform.run(ctx, {"count": 3, "slice_seconds": 5.0})


def test_output_artifact_optional_inputs():
    """output.artifact 可选输入缺失时不报错。"""
    from app.workflow.nodes import output_artifact

    result_obj = SimpleNamespace(
        highlight_video_path=None,
        report=None,
        report_path=None,
        highlight=None,
    )
    ctx = FakeContext(result=result_obj, inputs={})
    result = output_artifact.run(ctx, {})
    assert result == {}
    # 没有输入时不写入任何字段
    assert result_obj.highlight_video_path is None
