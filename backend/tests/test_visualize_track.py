"""轨迹可视化节点测试（post.visualize_track）。

覆盖：spec 契约（端口/stage/on_failure/persists）/ 参数 R10 边界 /
节点薄封装传参与输出路径 / 空轨迹诊断语义 / 纯渲染辅助函数 /
图校验接入（输出端口悬空合法）/ get_task_output_path 落库查询。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.config import AppConfig
from app.workflow.graph import WorkflowGraph
from app.workflow.spec import build_schema, get_spec, resolve_params

import app.workflow.nodes  # noqa: F401  自动发现注册全部内置节点

NODE = "post.visualize_track"


@dataclass
class FakeContext:
    """模拟执行器 Context，供节点函数调用。"""
    video_path: Path = field(default_factory=lambda: Path("/tmp/test.mp4"))
    config: AppConfig = field(default_factory=lambda: AppConfig())
    task_out: Path = field(default_factory=lambda: Path("/tmp/out"))
    task_id: str = "vis_test_01"
    level: str = "intermediate"
    inputs: dict = field(default_factory=dict)
    result: object = None


# ---------- spec 契约 ----------

def test_spec_registered_contract():
    """节点已注册：类别/阶段/失败策略/端口/落库声明与设计一致。"""
    spec = get_spec(NODE)
    assert spec is not None
    assert spec.category == "post"
    assert spec.stage == "postprocessing"
    assert spec.on_failure == "skip"          # 诊断节点，CV 环境缺失不拖垮任务
    assert spec.persists == (("video", "track_overlay"),)

    inputs = {p.name: p for p in spec.inputs}
    assert inputs["video"].required is True
    assert inputs["track"].required is True
    assert inputs["duration"].required is False   # 时间线总长可回退帧数推算
    assert [p.name for p in spec.outputs] == ["video"]

    schema_types = {n["type"] for n in build_schema() if not n["type"].startswith("test.")}
    assert NODE in schema_types


def test_params_r10_bounds():
    """参数越界/非法选项 → ValueError（图校验 R10 拦截）。"""
    spec = get_spec(NODE)
    with pytest.raises(ValueError, match="trail_seconds"):
        resolve_params(spec.params, {"trail_seconds": 99.0})
    with pytest.raises(ValueError, match="ball_conf"):
        resolve_params(spec.params, {"ball_conf": 2.0})
    with pytest.raises(ValueError, match="break_gap"):
        resolve_params(spec.params, {"break_gap": 99.0})
    with pytest.raises(ValueError, match="max_speed"):
        resolve_params(spec.params, {"max_speed": 10.0})
    with pytest.raises(ValueError, match="player_model"):
        resolve_params(spec.params, {"player_model": "xx"})


# ---------- 节点薄封装 ----------

def _fake_renderer(captured):
    def fake(video, track, out_path, **kw):
        captured.update(video=video, track=track, out_path=Path(out_path), **kw)
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.touch()
        return out
    return fake


def test_node_run_passthrough_params(tmp_path, monkeypatch):
    """参数全量透传到渲染器；输出固定 task_out/track_overlay.mp4。"""
    from app.utils import cv_visualize
    from app.workflow.nodes import post_visualize

    captured = {}
    monkeypatch.setattr(cv_visualize, "render_track_overlay", _fake_renderer(captured))

    pts = [{"t": 1.0, "x": 10, "y": 20, "confidence": 0.9}]
    ctx = FakeContext(
        task_out=tmp_path,
        inputs={"video": tmp_path / "v.mp4", "track": pts, "duration": 60.0},
    )
    out = post_visualize.run(ctx, {
        "show_players": False, "trail_seconds": 3.0, "ball_conf": 0.6,
        "break_gap": 0.3, "max_speed": 5000.0,
        "player_stride": 5, "player_model": "s", "device": "cpu",
    })

    assert out["video"] == tmp_path / "track_overlay.mp4"
    assert out["video"].is_file()
    assert captured["track"] is pts
    assert captured["duration"] == 60.0
    assert captured["show_players"] is False
    assert captured["trail_seconds"] == 3.0
    assert captured["ball_conf"] == 0.6
    assert captured["break_gap"] == 0.3
    assert captured["max_speed"] == 5000.0
    assert captured["player_stride"] == 5
    assert captured["model_size"] == "s"
    assert captured["device"] == "cpu"
    assert captured["task_id"] == "vis_test_01"


def test_node_missing_required_inputs(tmp_path):
    """video/track 未连接 → ValueError（必填输入契约）。"""
    from app.workflow.nodes import post_visualize

    with pytest.raises(ValueError, match="video"):
        post_visualize.run(FakeContext(task_out=tmp_path, inputs={"track": []}), {})
    with pytest.raises(ValueError, match="track"):
        post_visualize.run(
            FakeContext(task_out=tmp_path, inputs={"video": tmp_path / "v.mp4"}), {}
        )


def test_node_empty_track_still_renders(tmp_path, monkeypatch):
    """空轨迹仍渲染（全红时间线 =「球未被识别」的诊断证据），duration 缺省回退 0。"""
    from app.utils import cv_visualize
    from app.workflow.nodes import post_visualize

    captured = {}
    monkeypatch.setattr(cv_visualize, "render_track_overlay", _fake_renderer(captured))

    ctx = FakeContext(
        task_out=tmp_path,
        inputs={"video": tmp_path / "v.mp4", "track": []},
    )
    out = post_visualize.run(ctx, {
        "show_players": True, "trail_seconds": 2.0, "ball_conf": 0.5,
        "break_gap": 0.5, "max_speed": 10000.0,
        "player_stride": 3, "player_model": "n", "device": "auto",
    })
    assert captured["track"] == []
    assert captured["duration"] == 0.0
    assert out["video"].is_file()


# ---------- 纯渲染辅助函数（无 cv2 依赖） ----------

def test_nearest_point_and_window():
    from app.utils.cv_visualize import nearest_point, points_in_window

    pts = [{"t": t, "x": 1.0, "y": 1.0, "confidence": 0.9}
           for t in (1.0, 1.1, 1.2, 5.0)]
    times = [p["t"] for p in pts]

    assert nearest_point(times, pts, 1.15)["t"] == 1.1     # 命中最近前点
    assert nearest_point(times, pts, 0.9) is None          # 轨迹尚未开始
    assert nearest_point(times, pts, 4.0) is None          # 距最近点 2.8s > tol
    assert nearest_point(times, pts, 1.35, tol=0.1) is None  # 超出自定义容忍窗（1.35-1.2=0.15）

    win = points_in_window(times, pts, 0.0, 1.15)
    assert [p["t"] for p in win] == [1.0, 1.1]


def test_coverage_bins():
    from app.utils.cv_visualize import coverage_bins

    flags = coverage_bins([0.1, 0.6, 3.2], duration=4.0, bin_seconds=0.5)
    assert len(flags) == 8
    assert flags[0] and flags[1] and flags[6]
    assert not flags[2] and not flags[7]     # 1.0~3.0s 与末尾桶无检出

    assert coverage_bins([], 4.0) == []      # 无轨迹 → 跳过时间线
    assert coverage_bins([1.0], 0.0) == []   # 时长未知 → 跳过时间线


def test_coverage_bins_clamps_out_of_range():
    """轨迹点超出 duration（钳位到末桶），不越界。"""
    from app.utils.cv_visualize import coverage_bins

    flags = coverage_bins([99.0], duration=2.0, bin_seconds=0.5)
    assert len(flags) == 4
    assert flags[-1]


# ---------- 多球区分：tracklet 分段（纯函数） ----------

def test_split_tracklets_rules():
    """断线规则：连续同段 / 时间断线 / 速度断线（换球跳变） / 同刻异点 / 边界。"""
    from app.utils.cv_visualize import split_tracklets

    # 连续稠密点（150 px/s < 上限）→ 单段
    pts = [{"t": t, "x": 40.0 + 150 * (t - 1.0), "y": 110.0, "confidence": 0.9}
           for t in (1.0, 1.1, 1.2, 1.3)]
    segs = split_tracklets(pts, break_gap=0.5, max_speed=10000.0)
    assert len(segs) == 1 and segs[0] == pts

    # 时间断线：1.1s → 3.0s 间隔 1.9s > 0.5s
    gap_pts = [
        {"t": 1.0, "x": 0.0, "y": 0.0, "confidence": 0.9},
        {"t": 1.1, "x": 30.0, "y": 0.0, "confidence": 0.9},
        {"t": 3.0, "x": 60.0, "y": 0.0, "confidence": 0.9},
    ]
    assert [len(s) for s in split_tracklets(gap_pts, break_gap=0.5, max_speed=10000.0)] == [2, 1]

    # 速度断线：0.1s 内跳 5000px = 50000 px/s > 10000（球间跳变的典型特征）
    jump_pts = [
        {"t": 1.0, "x": 0.0, "y": 0.0, "confidence": 0.9},
        {"t": 1.1, "x": 5000.0, "y": 0.0, "confidence": 0.9},
    ]
    assert len(split_tracklets(jump_pts, break_gap=0.5, max_speed=10000.0)) == 2

    # 同一时刻异球点必断；同一时刻同位置点不断
    same_t = [
        {"t": 1.0, "x": 0.0, "y": 0.0, "confidence": 0.9},
        {"t": 1.0, "x": 500.0, "y": 0.0, "confidence": 0.9},
    ]
    assert len(split_tracklets(same_t)) == 2
    same_pt = [
        {"t": 1.0, "x": 0.0, "y": 0.0, "confidence": 0.9},
        {"t": 1.0, "x": 0.0, "y": 0.0, "confidence": 0.9},
    ]
    assert len(split_tracklets(same_pt)) == 1

    # 边界：空 / 单点
    assert split_tracklets([]) == []
    single = [{"t": 1.0, "x": 0.0, "y": 0.0, "confidence": 0.9}]
    assert split_tracklets(single) == [single]


def test_is_static_segment():
    """静止判定：长时间原地不动 → 静止球；移动/跨度不足/单点 → 非静止。"""
    from app.utils.cv_visualize import is_static_segment

    # 5s 内 ±1px 抖动 → 静止（地上的球）
    static = [{"t": t, "x": 100.0 + (i % 2), "y": 50.0, "confidence": 0.8}
              for i, t in enumerate((0.0, 1.0, 2.0, 3.0, 4.0))]
    assert is_static_segment(static, radius=10.0, min_span=3.0) is True

    # 持续移动（4s 内位移 200px）→ 非静止
    moving = [{"t": t, "x": 100.0 + 50 * t, "y": 50.0, "confidence": 0.8}
              for t in (0.0, 1.0, 2.0, 3.0, 4.0)]
    assert is_static_segment(moving, radius=10.0, min_span=3.0) is False

    # 位置不动但跨度 2s < 3s → 非静止（短暂停留不算）
    short = [{"t": t, "x": 100.0, "y": 50.0, "confidence": 0.8} for t in (0.0, 1.0, 2.0)]
    assert is_static_segment(short, radius=10.0, min_span=3.0) is False

    # 单点（span=0）不算静止
    assert is_static_segment([{"t": 0.0, "x": 1.0, "y": 1.0}]) is False


# ---------- 多球区分：真实渲染冒烟 ----------

def _count_color(img, bgr, tol=45) -> int:
    """统计与目标 BGR 色差 ≤ tol 的像素数（容忍有损编码偏移）。"""
    import numpy as np

    diff = np.abs(img.astype(int) - np.array(bgr))
    return int(np.sum(np.all(diff <= tol, axis=-1)))


def test_render_multi_ball_segments(tmp_path):
    """真实渲染冒烟：三球轨迹 → 段0青 / 段1品红折线、段2静止球灰标。"""
    cv2 = pytest.importorskip("cv2")
    pytest.importorskip("numpy")
    from app.utils import cv_visualize

    w, h, fps, seconds = 320, 240, 30, 6
    src = tmp_path / "in.mp4"
    writer = cv2.VideoWriter(str(src), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    assert writer.isOpened()
    import numpy as np
    for _ in range(fps * seconds):
        writer.write(np.zeros((h, w, 3), dtype=np.uint8))
    writer.release()

    # 单点/帧序列（TrackNet 契约）：球A(0~1s) → 跳变 → 球B(1~2s) → 跳变 → 静止球S(2~6s)
    track = []
    t = 0.0
    while t < 1.0 - 1e-6:
        track.append({"t": round(t, 3), "x": 40 + 150 * t, "y": 110.0, "confidence": 0.9})
        t += 1 / fps
    t = 1.0
    while t < 2.0 - 1e-6:
        tau = t - 1.0
        track.append({"t": round(t, 3), "x": 300 - 60 * tau, "y": 150 + 30 * tau,
                      "confidence": 0.85})
        t += 1 / fps
    t = 2.0
    while t < seconds - 0.05:
        track.append({"t": round(t, 3), "x": 60.0, "y": 180.0, "confidence": 0.8})
        t += 1 / fps

    out = cv_visualize.render_track_overlay(
        src, track, tmp_path / "overlay.mp4",
        duration=float(seconds), show_players=False,
        trail_seconds=2.0, ball_conf=0.5,
        break_gap=0.5, max_speed=3000.0,  # 收紧速度上限使测试内跳变必然断线
    )
    assert out.is_file()

    cap = cv2.VideoCapture(str(out))
    frames = {}
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx in (15, 45, 120):  # t≈0.5s / 1.5s / 4.0s
            frames[idx] = frame
        idx += 1
    cap.release()
    assert idx == fps * seconds

    # 检测区域裁剪到 HUD（y<74）与时间线（y≥232）之外，排除白字/色块干扰
    # t≈0.5s：球A 段折线（段0=青），无品红/灰线
    f0 = frames[15][80:225]
    assert _count_color(f0, (255, 255, 0)) > 20, "段0 青色折线应存在"
    assert _count_color(f0, (255, 0, 255)) == 0, "t≈0.5s 不应有段1 品红"
    assert _count_color(f0, (170, 170, 170)) == 0, "t≈0.5s 不应有静止灰标"

    # t≈1.5s：球A + 球B 都在回看窗内，段间断开且段1=品红
    f1 = frames[45][80:225]
    assert _count_color(f1, (255, 0, 255)) > 10, "段1 品红折线应存在"
    assert _count_color(f1, (170, 170, 170)) == 0, "静止段尚未开始"

    # t≈4.0s：静止球命中 → 灰标 static?；窗内仅段2，无青/品红折线
    f2 = frames[120][80:225]
    assert _count_color(f2, (170, 170, 170)) > 10, "静止球灰色标记应存在"
    assert _count_color(f2, (255, 255, 0)) == 0, "旧球折线不应再连"
    assert _count_color(f2, (255, 0, 255)) == 0, "换球折线不应再连"


# ---------- 图校验接入 ----------

def test_graph_with_visualize_node_validates():
    """可视化节点接入检测分支：输出端口悬空不违反 R9，图整体合法。"""
    g = WorkflowGraph(name="轨迹可视化")
    g.add_node("n1", "input.video")
    g.add_node("n2", "preprocess.transcode")
    g.add_node("n3", "detect.tracknet")
    g.add_node("n4", NODE)
    g.add_node("n5", "post.score_highlights")
    g.add_node("n6", "output.artifact")
    g.add_edge("n1", "video", "n2", "video")
    g.add_edge("n2", "video", "n3", "video")
    g.add_edge("n2", "duration", "n3", "duration")
    g.add_edge("n2", "video", "n4", "video")
    g.add_edge("n3", "track", "n4", "track")
    g.add_edge("n3", "candidates", "n5", "candidates")
    g.add_edge("n2", "video", "n5", "video")
    g.add_edge("n5", "highlight", "n6", "highlight")

    result = g.validate()
    assert result["ok"] is True, result.get("errors")


def test_graph_missing_track_edge_fails_r5():
    """track 未连线 → R5 必填输入阻断。"""
    g = WorkflowGraph(name="缺track")
    g.add_node("n1", "input.video")
    g.add_node("n2", "preprocess.transcode")
    g.add_node("n4", NODE)
    g.add_node("n6", "output.artifact")
    g.add_edge("n1", "video", "n2", "video")
    g.add_edge("n2", "video", "n4", "video")
    g.add_edge("n2", "video", "n6", "video")

    result = g.validate()
    assert result["ok"] is False
    assert any(e.get("code") == "R5" for e in result["errors"])


# ---------- 落库查询（下载端点依赖） ----------

def test_get_task_output_path_roundtrip(tmp_path):
    """record_task_output(track_overlay) → get_task_output_path 命中；错 kind 返回 None。"""
    from app.config import load_config
    from app.services import db_service
    import importlib

    cfg = load_config()
    data_dir = cfg.data_path
    data_dir.mkdir(parents=True, exist_ok=True)
    test_db = data_dir / "test_visualize.db"
    if test_db.exists():
        test_db.unlink()
    cfg.database.url = f"sqlite:///{test_db}"

    importlib.reload(db_service)
    db_service.init_db(cfg)

    task_id = "vis_artifact_01"
    db_service.record_task_start(task_id, "in.mp4", "intermediate")
    fake_video = data_dir / "vis.mp4"
    fake_video.write_bytes(b"\x00")
    db_service.record_task_output(task_id, "track_overlay", str(fake_video), 0.01)

    assert db_service.get_task_output_path(task_id, "track_overlay") == str(fake_video)
    assert db_service.get_task_output_path(task_id, "report") is None
    assert db_service.get_task_output_path("no_such_task", "track_overlay") is None
