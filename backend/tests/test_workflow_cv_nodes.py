"""Step 2 测试：CV 感知层 6 节点（方案 12 · 2.11 + 2.12 自动化部分）。

覆盖：schema 15 节点 / 端口与 on_failure 契约 / 参数边界（R10）/
图校验 R4·R5·R8（新端口类型）/ 节点薄封装传参与函数式传递 /
CV 缺依赖降级（CvUnavailable）/ dtw 分类可测路径 /
示例 B 并联对比图校验、示例 C 图端到端执行、detect 失败 skip 级联。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import AppConfig
from app.models import HighlightResult, Segment
from app.utils.cv_runtime import CvUnavailable
from app.workflow.graph import WorkflowGraph
from app.workflow.spec import build_schema, get_spec, resolve_params

import app.workflow.nodes  # noqa: F401  自动发现注册全部内置节点


NEW_NODE_TYPES = [
    "detect.tracknet", "detect.court", "detect.player", "detect.pose",
    "post.score_highlights", "post.classify_strokes",
]
DETECT_NODES = ["detect.tracknet", "detect.court", "detect.player", "detect.pose"]
POST_NODES = ["post.score_highlights", "post.classify_strokes"]


@dataclass
class FakeContext:
    """模拟执行器 Context，供节点函数调用。"""
    video_path: Path = field(default_factory=lambda: Path("/tmp/test.mp4"))
    config: AppConfig = field(default_factory=lambda: AppConfig())
    task_out: Path = field(default_factory=lambda: Path("/tmp/out"))
    task_id: str = "cv_test_01"
    level: str = "intermediate"
    inputs: dict = field(default_factory=dict)
    result: object = None


def _builtin_schema() -> list[dict]:
    """内置节点 schema（剔除其他测试模块注册的 test.* 节点）。"""
    return [n for n in build_schema() if not n["type"].startswith("test.")]


def _example_c_graph() -> WorkflowGraph:
    """示例 C（降本链路）：input → transcode → tracknet → score → artifact。"""
    g = WorkflowGraph(name="示例C")
    g.add_node("n1", "input.video")
    g.add_node("n2", "preprocess.transcode")
    g.add_node("n3", "detect.tracknet")
    g.add_node("n4", "post.score_highlights")
    g.add_node("n5", "output.artifact")
    g.add_edge("n1", "video", "n2", "video")
    g.add_edge("n2", "video", "n3", "video")
    g.add_edge("n2", "duration", "n3", "duration")
    g.add_edge("n2", "video", "n4", "video")
    g.add_edge("n3", "candidates", "n4", "candidates")
    g.add_edge("n4", "highlight", "n5", "highlight")
    return g


def _example_c_dict() -> dict:
    return {
        "version": 1,
        "name": "示例C",
        "nodes": [
            {"id": "n1", "type": "input.video", "params": {}},
            {"id": "n2", "type": "preprocess.transcode", "params": {}},
            {"id": "n3", "type": "detect.tracknet", "params": {}},
            {"id": "n4", "type": "post.score_highlights", "params": {}},
            {"id": "n5", "type": "output.artifact", "params": {}},
        ],
        "edges": [
            {"id": "e1", "from": ["n1", "video"], "to": ["n2", "video"]},
            {"id": "e2", "from": ["n2", "video"], "to": ["n3", "video"]},
            {"id": "e3", "from": ["n2", "duration"], "to": ["n3", "duration"]},
            {"id": "e4", "from": ["n2", "video"], "to": ["n4", "video"]},
            {"id": "e5", "from": ["n3", "candidates"], "to": ["n4", "candidates"]},
            {"id": "e6", "from": ["n4", "highlight"], "to": ["n5", "highlight"]},
        ],
    }


# ---------- 2.11: schema 返回 15 个内置节点 ----------

def test_schema_has_15_builtin_nodes():
    """内置节点共 15 个（9 旧 + 6 新），新节点全部在目录中。"""
    schema = _builtin_schema()
    types = {n["type"] for n in schema}
    assert len(schema) == 15, f"期望 15 个内置节点，实际 {len(schema)}"
    assert set(NEW_NODE_TYPES) <= types


def test_new_node_ports_stage_and_on_failure():
    """新节点端口类型 / stage / 失败策略与方案 4.3.2 规格一致。"""
    # 4 个 detect 节点：stage=detecting、on_failure=skip（降级不拖垮任务）
    for t in DETECT_NODES:
        spec = get_spec(t)
        assert spec is not None, t
        assert spec.stage == "detecting"
        assert spec.on_failure == "skip"
    # 2 个 post 节点：stage=postprocessing、on_failure=fail（方案 12 · 2.8 明确仅 detect 标 skip）
    for t in POST_NODES:
        spec = get_spec(t)
        assert spec is not None, t
        assert spec.stage == "postprocessing"
        assert spec.on_failure == "fail"

    tn = get_spec("detect.tracknet")
    assert {p.name: p.type for p in tn.inputs} == {"video": "video", "duration": "duration"}
    assert {p.name: p.type for p in tn.outputs} == {"track": "track", "candidates": "candidates"}

    ct = get_spec("detect.court")
    assert [(p.name, p.type) for p in ct.inputs] == [("video", "video")]
    assert [(p.name, p.type) for p in ct.outputs] == [("court", "court")]

    pl = get_spec("detect.player")
    assert {p.name: p.type for p in pl.outputs} == {"candidates": "candidates"}

    po = get_spec("detect.pose")
    assert {p.name: p.type for p in po.outputs} == {"pose": "pose"}

    sc = get_spec("post.score_highlights")
    assert {p.name: p.type for p in sc.inputs} == {"candidates": "candidates", "video": "video"}
    assert {p.name: p.type for p in sc.outputs} == {"highlight": "highlight"}

    # post.classify_strokes：pose 可选（无姿态降级 cnn 纯视觉分类）
    cf = get_spec("post.classify_strokes")
    assert {p.name: p.type for p in cf.inputs} == {"highlight": "highlight", "pose": "pose"}
    assert {p.name: p.required for p in cf.inputs} == {"highlight": True, "pose": False}


# ---------- 2.11: 参数边界（R10 同源 resolve_params） ----------

def test_new_node_param_defaults_and_boundaries():
    """新节点参数缺省可解析；越界/非法选项抛 ValueError（与图校验 R10 同源）。"""
    tn = get_spec("detect.tracknet")
    defaults = resolve_params(tn.params, {})
    assert defaults == {
        "confidence": 0.5, "cluster_gap": 4.0, "top_n": 5, "frame_stride": 1,
    }
    with pytest.raises(ValueError, match="confidence"):
        resolve_params(tn.params, {"confidence": 1.5})
    with pytest.raises(ValueError, match="frame_stride"):
        resolve_params(tn.params, {"frame_stride": 0})
    with pytest.raises(ValueError, match="cluster_gap"):
        resolve_params(tn.params, {"cluster_gap": 0.1})

    ct = get_spec("detect.court")
    with pytest.raises(ValueError, match="interval"):
        resolve_params(ct.params, {"interval": 0})

    pl = get_spec("detect.player")
    with pytest.raises(ValueError, match="model_size"):
        resolve_params(pl.params, {"model_size": "x"})
    with pytest.raises(ValueError, match="motion_percentile"):
        resolve_params(pl.params, {"motion_percentile": 40})

    sc = get_spec("post.score_highlights")
    with pytest.raises(ValueError, match="max_segments"):
        resolve_params(sc.params, {"max_segments": 0})
    with pytest.raises(ValueError, match="min_score"):
        resolve_params(sc.params, {"min_score": 1.5})

    cf = get_spec("post.classify_strokes")
    with pytest.raises(ValueError, match="classifier"):
        resolve_params(cf.params, {"classifier": "svm"})


# ---------- 2.11: 图校验（R4 / R5 / R8） ----------

def test_graph_validate_example_c_ok():
    """示例 C 图（tracknet + CLIP 初筛）通过 R1~R10 全部校验。"""
    res = _example_c_graph().validate()
    assert res["ok"], res["errors"]


def test_graph_validate_example_b_parallel_compare():
    """示例 B 并联对比：旧 detect.candidates 与新 detect.tracknet 各接独立 analyze，校验通过。"""
    g = WorkflowGraph(name="示例B")
    g.add_node("n1", "input.video")
    g.add_node("n2", "preprocess.transcode")
    g.add_node("n3", "detect.candidates")
    g.add_node("n4", "detect.tracknet")
    g.add_node("n5", "analyze.highlight")
    g.add_node("n6", "analyze.highlight", params={"model": ""})
    g.add_node("n7", "output.artifact")
    g.add_edge("n1", "video", "n2", "video")
    g.add_edge("n2", "video", "n3", "video")
    g.add_edge("n2", "duration", "n3", "duration")
    g.add_edge("n2", "video", "n4", "video")
    g.add_edge("n2", "duration", "n4", "duration")
    g.add_edge("n2", "video", "n5", "video")
    g.add_edge("n2", "duration", "n5", "duration")
    g.add_edge("n3", "candidates", "n5", "candidates")
    g.add_edge("n2", "video", "n6", "video")
    g.add_edge("n2", "duration", "n6", "duration")
    g.add_edge("n4", "candidates", "n6", "candidates")
    g.add_edge("n5", "highlight", "n7", "highlight")
    res = g.validate()
    assert res["ok"], res["errors"]


def test_graph_r4_track_to_candidates_type_mismatch():
    """R4：track 端口不能连到 candidates 输入（新端口类型参与连线校验）。"""
    g = _example_c_graph()
    # 用 track 输出替换 candidates 输出的连线
    g.edges = [e for e in g.edges if not (e.from_node_id == "n3" and e.from_port == "candidates")]
    g.add_edge("n3", "track", "n4", "candidates")
    res = g.validate()
    r4 = [e for e in res["errors"] if e["code"] == "R4"]
    assert r4, "track → candidates 应触发 R4 类型不匹配"
    assert "track" in r4[0]["message"] and "candidates" in r4[0]["message"]


def test_graph_r5_score_requires_candidates():
    """R5：post.score_highlights 的必填输入 candidates 未连接 → 阻断。"""
    g = _example_c_graph()
    g.edges = [e for e in g.edges if not (e.to_node_id == "n4" and e.to_port == "candidates")]
    res = g.validate()
    r5 = [e for e in res["errors"] if e["code"] == "R5"]
    assert r5 and "candidates" in r5[0]["message"]


def test_graph_r8_score_producer_requires_artifact_highlight_edge():
    """R8：图中有 highlight 生产节点（post.score_highlights）时 artifact 缺 highlight 入边 → 阻断。"""
    g = _example_c_graph()
    g.edges = [e for e in g.edges if not (e.to_node_id == "n5" and e.to_port == "highlight")]
    res = g.validate()
    r8 = [e for e in res["errors"] if e["code"] == "R8"]
    assert r8 and "highlight" in r8[0]["message"]


# ---------- 节点行为：detect.tracknet ----------

def test_tracknet_node_aggregates_track_to_candidates(tmp_path, monkeypatch):
    """tracknet 节点：透传参数 → cv_tracknet 轨迹 → event_detect 聚合候选。"""
    from app.utils import cv_tracknet
    from app.workflow.nodes import detect_tracknet

    captured = {}
    fake_points = [
        {"t": t, "x": 100.0, "y": 50.0, "confidence": 0.9}
        for t in (1.0, 1.5, 2.0, 20.0, 20.5)
    ]

    def fake_track(video, duration, confidence=0.5, frame_stride=1, **kw):
        captured.update(confidence=confidence, frame_stride=frame_stride, duration=duration)
        return fake_points

    monkeypatch.setattr(cv_tracknet, "track_video", fake_track)

    config = AppConfig()
    ctx = FakeContext(
        video_path=tmp_path / "v.mp4", config=config,
        inputs={"video": tmp_path / "v.mp4", "duration": 60.0},
    )
    out = detect_tracknet.run(ctx, {
        "confidence": 0.6, "cluster_gap": 4.0, "top_n": 5, "frame_stride": 2,
    })

    assert captured == {"confidence": 0.6, "frame_stride": 2, "duration": 60.0}
    # cluster_gap 写入节点级 config（执行器注入的深拷贝）
    assert config.highlight.hit_cluster_gap_seconds == 4.0
    assert out["track"] == fake_points
    # 两簇轨迹（1~2s、20~20.5s，间隔 18s > gap 4s）→ 两个候选窗口
    cands = out["candidates"]
    assert len(cands) == 2
    assert cands[0].start < 3.0 and cands[0].end <= 4.5
    assert cands[1].start >= 17.0  # hit_window_expand=2 外扩后 ≥ 18
    assert all(c.label == "candidate" for c in cands)


def test_tracknet_node_tracktop_candidates_topn_zero放开(tmp_path, monkeypatch):
    """top_n=0 → 传 None 放开上限（与 detect.candidates 语义一致）。"""
    from app.services import event_detect
    from app.workflow.nodes import detect_tracknet
    from app.utils import cv_tracknet

    monkeypatch.setattr(
        cv_tracknet, "track_video",
        lambda *a, **k: [{"t": t, "x": 1.0, "y": 1.0, "confidence": 0.9}
                         for t in (1.0, 2.0, 20.0, 21.0, 40.0, 41.0)],
    )
    captured = {}
    real = event_detect.track_to_candidates

    def spy(track, duration, config, top_n=None):
        captured["top_n"] = top_n
        return real(track, duration, config, top_n=top_n)

    monkeypatch.setattr(event_detect, "track_to_candidates", spy)

    ctx = FakeContext(
        video_path=tmp_path / "v.mp4",
        inputs={"video": tmp_path / "v.mp4", "duration": 60.0},
    )
    detect_tracknet.run(ctx, {
        "confidence": 0.5, "cluster_gap": 4.0, "top_n": 0, "frame_stride": 1,
    })
    assert captured["top_n"] is None


def test_tracknet_missing_deps_raises_cvunavailable(tmp_path):
    """无 CV 环境（缺 torch/权重/视频）→ CvUnavailable，由 on_failure=skip 级联降级。"""
    from app.workflow.nodes import detect_tracknet

    ctx = FakeContext(inputs={"video": tmp_path / "nope.mp4", "duration": 60.0})
    with pytest.raises(CvUnavailable, match=r"CV 依赖|权重|不可读"):
        detect_tracknet.run(ctx, {
            "confidence": 0.5, "cluster_gap": 4.0, "top_n": 5, "frame_stride": 1,
        })


# ---------- 节点行为：detect.player / detect.court / detect.pose ----------

def test_player_node_aggregates_motion_scores(tmp_path, monkeypatch):
    """player 节点：运动分数 → event_detect.scores_to_candidates 聚合候选窗口。"""
    from app.utils import cv_player
    from app.workflow.nodes import detect_player

    scores = [0.0] * 40
    scores[10] = scores[11] = scores[12] = 1.0      # 爆发 1：t=10~13
    scores[29] = scores[30] = scores[31] = 0.9      # 爆发 2：t=29~32
    captured = {}

    def fake_scores(video, duration, model_size="n", **kw):
        captured.update(model_size=model_size, duration=duration)
        return scores, 1.0

    monkeypatch.setattr(cv_player, "compute_motion_scores", fake_scores)

    ctx = FakeContext(
        video_path=tmp_path / "v.mp4",
        inputs={"video": tmp_path / "v.mp4", "duration": 60.0},
    )
    out = detect_player.run(ctx, {
        "model_size": "s", "motion_percentile": 80, "top_n": 5,
    })
    assert captured == {"model_size": "s", "duration": 60.0}
    cands = out["candidates"]
    assert len(cands) == 2
    assert cands[0].start == pytest.approx(10.0, abs=0.6)
    assert cands[1].start == pytest.approx(29.0, abs=0.6)


def test_court_node_passthrough_params(tmp_path, monkeypatch):
    """court 节点：refine/interval 透传，court dict 原样输出。"""
    from app.utils import cv_court
    from app.workflow.nodes import detect_court

    captured = {}

    def fake_court(video, refine=True, interval=30, weights=""):
        captured.update(refine=refine, interval=interval)
        return {"points": [[0.0, 0.0]] * 14, "homography": None}

    monkeypatch.setattr(cv_court, "detect_court", fake_court)

    ctx = FakeContext(video_path=tmp_path / "v.mp4", inputs={"video": tmp_path / "v.mp4"})
    out = detect_court.run(ctx, {"refine": False, "interval": 60})
    assert captured == {"refine": False, "interval": 60}
    assert len(out["court"]["points"]) == 14
    assert out["court"]["homography"] is None


def test_pose_node_passthrough_params(tmp_path, monkeypatch):
    """pose 节点：max_people/fps 透传，pose dict 原样输出。"""
    from app.utils import cv_pose
    from app.workflow.nodes import detect_pose

    captured = {}

    def fake_pose(video, max_people=2, fps=15.0, weights=""):
        captured.update(max_people=max_people, fps=fps)
        return {"fps": fps, "frames": []}

    monkeypatch.setattr(cv_pose, "extract_poses", fake_pose)

    ctx = FakeContext(video_path=tmp_path / "v.mp4", inputs={"video": tmp_path / "v.mp4"})
    out = detect_pose.run(ctx, {"max_people": 4, "fps": 10.0})
    assert captured == {"max_people": 4, "fps": 10.0}
    assert out["pose"]["fps"] == 10.0


# ---------- 节点行为：post.score_highlights ----------

def test_score_node_filters_truncates_and_scores(tmp_path, monkeypatch):
    """score 节点：min_score 过滤、max_segments 截断、score 写入 confidence（函数式）。"""
    from app.utils import cv_clip_score
    from app.workflow.nodes import post_score

    segs = [
        Segment(start=0, end=4, label="candidate", confidence=0.9),
        Segment(start=10, end=14, label="candidate", confidence=0.9),
        Segment(start=20, end=24, label="candidate", confidence=0.9),
    ]
    captured = {}

    def fake_score(video, windows, model="small", phrases="", **kw):
        captured.update(model=model, phrases=phrases, n=len(windows))
        # 故意乱序 + 一个低于阈值的分数
        return [(segs[0], 0.7), (segs[2], 0.5), (segs[1], 0.1)]

    monkeypatch.setattr(cv_clip_score, "score_windows", fake_score)

    ctx = FakeContext(
        video_path=tmp_path / "v.mp4",
        inputs={"video": tmp_path / "v.mp4", "candidates": segs},
    )
    out = post_score.run(ctx, {
        "clip_model": "base", "min_score": 0.25, "max_segments": 3,
        "highlight_phrases": "ace, smash",
    })
    assert captured["model"] == "base" and captured["phrases"] == "ace, smash"
    hl = out["highlight"]
    assert isinstance(hl, HighlightResult)
    # 0.1 被过滤，0.7/0.5 保留且按分数降序；score 写入 confidence
    assert [s.confidence for s in hl.segments] == [0.7, 0.5]
    # 函数式：原候选 confidence 未被改写
    assert [s.confidence for s in segs] == [0.9, 0.9, 0.9]
    assert hl.target_duration == 15

    # max_segments=1 → 截断到最高分段
    out1 = post_score.run(ctx, {
        "clip_model": "small", "min_score": 0.25, "max_segments": 1,
        "highlight_phrases": "ace",
    })
    assert len(out1["highlight"].segments) == 1
    assert out1["highlight"].segments[0].start == 0


def test_score_node_missing_candidates_raises_valueerror(tmp_path):
    """score 节点缺必填输入 → 清晰 ValueError。"""
    from app.workflow.nodes import post_score

    ctx = FakeContext(inputs={"video": tmp_path / "v.mp4"})
    with pytest.raises(ValueError, match="score_highlights.*candidates"):
        post_score.run(ctx, {
            "clip_model": "small", "min_score": 0.25, "max_segments": 3,
            "highlight_phrases": "x",
        })


# ---------- 节点行为：post.classify_strokes ----------

def test_classify_node_rewrites_label_functionally(tmp_path, monkeypatch):
    """classify 节点：置信度达标回写 label、不达标保留原 label，且不改上游对象。"""
    from app.utils import cv_stroke_cls
    from app.workflow.nodes import post_classify

    original = HighlightResult(segments=[
        Segment(start=0, end=4, label="forehand", confidence=0.9),
        Segment(start=10, end=14, label="other", confidence=0.4),
    ])
    captured = {}

    def fake_classify(video, segments, pose=None, classifier="cnn", weights=""):
        captured.update(video=video, classifier=classifier, pose=pose, n=len(segments))
        return {0: ("backhand", 0.95), 1: ("serve", 0.3)}

    monkeypatch.setattr(cv_stroke_cls, "classify_segments", fake_classify)

    ctx = FakeContext(
        video_path=tmp_path / "v.mp4",
        inputs={"highlight": original, "pose": {"fps": 15, "frames": []}},
    )
    out = post_classify.run(ctx, {"classifier": "dtw", "min_confidence": 0.5})

    assert captured["classifier"] == "dtw"
    assert captured["pose"] == {"fps": 15, "frames": []}
    assert captured["video"] == tmp_path / "v.mp4"
    segs = out["highlight"].segments
    assert segs[0].label == "backhand"          # 0.95 ≥ 0.5 → 覆盖
    assert segs[1].label == "other"             # 0.3 < 0.5 → 保留原 label
    assert segs[0] is not original.segments[0]  # 函数式 model_copy，非原地修改
    assert original.segments[0].label == "forehand"  # 上游对象未被污染


def test_classify_node_empty_highlight_skips_inference(tmp_path, monkeypatch):
    """空 highlight 不触发推理（不浪费一次模型调用）。"""
    from app.utils import cv_stroke_cls
    from app.workflow.nodes import post_classify

    def boom(*a, **k):
        raise AssertionError("空段不应触发分类推理")

    monkeypatch.setattr(cv_stroke_cls, "classify_segments", boom)
    ctx = FakeContext(inputs={"highlight": HighlightResult(segments=[])})
    out = post_classify.run(ctx, {"classifier": "cnn", "min_confidence": 0.5})
    assert out["highlight"].segments == []


def test_classify_dtw_without_pose_raises_valueerror(tmp_path):
    """dtw 分类器缺 pose 输入 → 可修复的连线错误（ValueError），提示连线或改 cnn。"""
    from app.workflow.nodes import post_classify

    ctx = FakeContext(inputs={
        "highlight": HighlightResult(segments=[Segment(start=0, end=4, label="rally", confidence=0.9)]),
    })
    with pytest.raises(ValueError, match=r"dtw.*pose"):
        post_classify.run(ctx, {"classifier": "dtw", "min_confidence": 0.5})


# ---------- cv_stroke_cls：dtw 可测路径（纯 Python） ----------

def test_dtw_distance_identical_and_empty():
    """DTW：相同序列距离 0；空序列距离 inf；不同序列距离 > 0。"""
    from app.utils.cv_stroke_cls import dtw_distance

    seq = [[0.0, 0.0], [1.0, 1.0], [2.0, 0.5]]
    assert dtw_distance(seq, seq) == pytest.approx(0.0)
    assert dtw_distance([], seq) == float("inf")
    assert dtw_distance(seq, [[5.0, 5.0], [6.0, 6.0]]) > 0


def test_dtw_classify_with_templates(tmp_path):
    """dtw 分类端到端（无重依赖）：模板库 JSON + 假 pose → 正确标签与高置信度。"""
    from app.utils.cv_stroke_cls import classify_segments

    T = 40

    def make_seq(increasing: bool) -> list:
        """一帧 33 关键点 [x,y,z]；x 随时间单调变化（forehand 增 / backhand 减）。"""
        frames = []
        for i in range(T):
            frac = i / (T - 1)
            x0 = frac if increasing else 1.0 - frac
            frames.append([
                [round(x0 + lm * 0.001, 4), round(lm * 0.02, 4), 0.0]
                for lm in range(33)
            ])
        return frames

    templates = {"forehand": [make_seq(True)], "backhand": [make_seq(False)]}
    tpl_path = tmp_path / "stroke_templates.json"
    tpl_path.write_text(json.dumps(templates), encoding="utf-8")

    # 假 pose：t=0~7s 每 0.25s 一帧，x 递增（与 forehand 模板同构）
    frames = []
    n = 29
    for i in range(n):
        frac = i / (n - 1)
        frames.append({
            "t": round(i * 0.25, 3),
            "people": [{
                "landmarks": [
                    [round(frac + lm * 0.001, 4), round(lm * 0.02, 4), 0.0]
                    for lm in range(33)
                ],
            }],
        })
    pose = {"fps": 4.0, "frames": frames}
    segments = [Segment(start=1.0, end=5.0, label="rally", confidence=0.9)]

    preds = classify_segments(
        Path("/tmp/v.mp4"), segments, pose=pose,
        classifier="dtw", weights=str(tpl_path),
    )
    assert 0 in preds
    label, conf = preds[0]
    assert label == "forehand", f"dtw 应匹配 forehand 模板，实际 {label}"
    assert conf >= 0.5


def test_classify_unknown_classifier_valueerror():
    """未知分类器 → ValueError（参数选项之外的运行时防线）。"""
    from app.utils.cv_stroke_cls import classify_segments

    with pytest.raises(ValueError, match="svm"):
        classify_segments(Path("/tmp/v.mp4"), [], classifier="svm")


# ---------- 2.12: 端到端（自动化：mock 推理，手工画布对比待权重环境） ----------

def _stub_preprocess(monkeypatch, tmp_path):
    from app.services import db_service, preprocess as pre_svc

    def fake_preprocess(video_path, config, work_dir=None):
        out = tmp_path / "processed.mp4"
        out.write_bytes(b"processed")
        return out

    monkeypatch.setattr(pre_svc, "preprocess", fake_preprocess)
    monkeypatch.setattr(
        pre_svc, "probe_video",
        lambda p: {"duration": 60.5, "width": 1280, "height": 720, "fps": 30.0},
    )
    monkeypatch.setattr(db_service, "record_task_input", lambda **kw: None)


def _make_result(task_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        task_id=task_id, stage="pending", status="pending",
        highlight=None, report=None, highlight_video_path=None,
        report_path=None, report_error=None, error=None, elapsed_seconds=0.0,
    )


def test_executor_example_c_end_to_end(tmp_path, monkeypatch):
    """示例 C 图端到端：真实节点执行（CV 推理打桩）→ 状态 succeeded、产物投影。"""
    from app.utils import cv_clip_score, cv_tracknet
    from app.workflow.executor import Executor
    from app.workflow.graph import WorkflowGraph

    _stub_preprocess(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cv_tracknet, "track_video",
        lambda *a, **k: [{"t": t, "x": 100.0, "y": 50.0, "confidence": 0.92}
                         for t in (5.0, 5.5, 6.0)],
    )

    def fake_score(video, windows, **kw):
        return [(windows[0], 0.8)] if windows else []

    monkeypatch.setattr(cv_clip_score, "score_windows", fake_score)

    graph = WorkflowGraph.from_dict(_example_c_dict())
    validation = graph.validate()
    assert validation["ok"], validation["errors"]

    result = _make_result("e2e_example_c")
    executor = Executor(graph, AppConfig(), result)
    executor.execute(tmp_path / "source.mp4")

    assert result.status == "succeeded", result.error
    assert result.highlight is not None
    assert len(result.highlight.segments) == 1
    assert result.highlight.segments[0].confidence == pytest.approx(0.8)
    statuses = {n.node_id: n.status for n in result.workflow_nodes}
    assert statuses == {
        "n1": "done", "n2": "done", "n3": "done", "n4": "done", "n5": "done",
    }


def test_executor_tracknet_unavailable_cascades_skip(tmp_path, monkeypatch):
    """CV 节点 CvUnavailable → on_failure=skip 级联跳过下游，任务仍 succeeded。"""
    from app.utils import cv_tracknet
    from app.workflow.executor import Executor
    from app.workflow.graph import WorkflowGraph

    _stub_preprocess(monkeypatch, tmp_path)

    def unavailable(*a, **k):
        raise CvUnavailable("缺少 CV 依赖 torch（测试桩）")

    monkeypatch.setattr(cv_tracknet, "track_video", unavailable)

    g = {
        "version": 1, "name": "skip级联",
        "nodes": [
            {"id": "n1", "type": "input.video", "params": {}},
            {"id": "n2", "type": "preprocess.transcode", "params": {}},
            {"id": "n3", "type": "detect.tracknet", "params": {}},
            {"id": "n4", "type": "analyze.highlight", "params": {}},
            {"id": "n5", "type": "output.artifact", "params": {}},
        ],
        "edges": [
            {"id": "e1", "from": ["n1", "video"], "to": ["n2", "video"]},
            {"id": "e2", "from": ["n2", "video"], "to": ["n3", "video"]},
            {"id": "e3", "from": ["n2", "duration"], "to": ["n3", "duration"]},
            {"id": "e4", "from": ["n2", "video"], "to": ["n4", "video"]},
            {"id": "e5", "from": ["n2", "duration"], "to": ["n4", "duration"]},
            {"id": "e6", "from": ["n3", "candidates"], "to": ["n4", "candidates"]},
            {"id": "e7", "from": ["n4", "highlight"], "to": ["n5", "highlight"]},
        ],
    }
    graph = WorkflowGraph.from_dict(g)

    result = _make_result("e2e_skip_cascade")
    executor = Executor(graph, AppConfig(), result)
    executor.execute(tmp_path / "source.mp4")

    assert result.status == "succeeded", result.error
    statuses = {n.node_id: n.status for n in result.workflow_nodes}
    assert statuses["n1"] == "done" and statuses["n2"] == "done"
    assert statuses["n3"] == "skipped", "CV 节点失败应按 on_failure=skip 标记"
    assert statuses["n4"] == "skipped", "candidates 来源死亡 → analyze 级联跳过"
    assert statuses["n5"] == "skipped", "highlight 来源死亡 → artifact 级联跳过"
    assert result.highlight is None
