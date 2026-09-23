"""批次 C 单元测试：topo_levels 分层 · 同层并行 · 声明式落库（文档 12 · 0.8）。"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from app.config import AppConfig
from app.models import HighlightResult, Segment, TaskStatus
from app.services import db_service
from app.workflow import spec as spec_mod
from app.workflow.executor import Context, Executor
from app.workflow.graph import WorkflowGraph

import app.workflow.nodes  # noqa: F401  确保所有内置节点已注册


# ---------- 工具 ----------

def _graph(nodes: list[dict], edges: list[dict], name: str = "test") -> WorkflowGraph:
    return WorkflowGraph.from_dict({"version": 1, "name": name, "nodes": nodes, "edges": edges})


def _node(nid: str, ntype: str, enabled: bool = True) -> dict:
    return {"id": nid, "type": ntype, "params": {}, "enabled": enabled}


def _edge(eid: str, src: str, src_port: str, dst: str, dst_port: str) -> dict:
    return {"id": eid, "from": [src, src_port], "to": [dst, dst_port]}


def _result(task_id: str = "batch_c") -> SimpleNamespace:
    return SimpleNamespace(
        task_id=task_id, stage="pending", status="pending",
        highlight=None, report=None, highlight_video_path=None,
        report_path=None, report_error=None, error=None, elapsed_seconds=0.0,
    )


def _stub_task_db(monkeypatch, inputs=None, outputs=None) -> None:
    """桩掉任务生命周期/落库写（避免测试触碰真实 DB），可选捕获 input/output 调用。"""
    monkeypatch.setattr(db_service, "record_task_start", lambda *a, **kw: None)
    monkeypatch.setattr(db_service, "record_task_finish", lambda **kw: None)
    if inputs is not None:
        monkeypatch.setattr(db_service, "record_task_input", lambda **kw: inputs.append(kw))
    if outputs is not None:
        monkeypatch.setattr(db_service, "record_task_output",
                            lambda *a, **kw: outputs.append((a, kw)))


# ---------- topo_levels 分层 ----------

class TestTopoLevels:
    def test_diamond_layering(self):
        """菱形图分层：依赖层递进，同层 id 升序。"""
        g = _graph(
            [_node("n1", "input.video"), _node("n2", "detect.court"),
             _node("n3", "detect.pose"), _node("n4", "output.artifact")],
            [_edge("e1", "n1", "video", "n2", "video"),
             _edge("e2", "n1", "video", "n3", "video"),
             _edge("e3", "n2", "court", "n4", "video"),
             _edge("e4", "n3", "pose", "n4", "video")],
        )
        assert g.topo_levels() == [["n1"], ["n2", "n3"], ["n4"]]

    def test_independent_node_sorts_into_level0(self):
        """独立节点 n9 与 n1 同层（层内 id 升序），展平后是合法拓扑序。"""
        g = _graph(
            [_node("n1", "input.video"), _node("n2", "detect.court"), _node("n9", "detect.pose")],
            [_edge("e1", "n1", "video", "n2", "video")],
        )
        levels = g.topo_levels()
        assert levels == [["n1", "n9"], ["n2"]]

        flat = [nid for lv in levels for nid in lv]
        pos = {nid: i for i, nid in enumerate(flat)}
        for e in g.edges:
            assert pos[e.from_node_id] < pos[e.to_node_id], f"边 {e.id} 违反拓扑序"

    def test_cycle_raises(self):
        """存在环 → ValueError（与 topo_order 一致）。"""
        g = _graph(
            [_node("n1", "detect.court"), _node("n2", "detect.pose")],
            [_edge("e1", "n1", "video", "n2", "video"),
             _edge("e2", "n2", "pose", "n1", "video")],
        )
        with pytest.raises(ValueError, match="环"):
            g.topo_levels()

    def test_disabled_nodes_excluded(self):
        """disabled 节点不参与分层（与 topo_order 语义一致）。"""
        g = _graph(
            [_node("n1", "input.video"), _node("n2", "detect.court"),
             _node("n3", "detect.pose", enabled=False), _node("n4", "output.artifact")],
            [_edge("e1", "n1", "video", "n2", "video"),
             _edge("e2", "n1", "video", "n3", "video"),
             _edge("e3", "n2", "court", "n4", "video"),
             _edge("e4", "n3", "pose", "n4", "video")],
        )
        assert g.topo_levels() == [["n1"], ["n2"], ["n4"]]


# ---------- 批次 C1：同层并行执行 ----------

def test_same_level_nodes_execute_in_parallel(tmp_path, monkeypatch):
    """同层两节点必须并发执行。

    两个 fake 节点在 threading.Barrier(2) 相会：并行 → 双方立即放行，状态 done；
    若退化为串行 → 先行者超时 BrokenBarrier，两节点均失败（on_failure=skip）→ skipped。
    """
    barrier = threading.Barrier(2, timeout=10)

    def fake_court(ctx, params):
        barrier.wait(timeout=10)
        return {"court": {"points": [[0, 0]] * 14}}

    def fake_pose(ctx, params):
        barrier.wait(timeout=10)
        return {"pose": {"fps": 15.0, "frames": []}}

    monkeypatch.setitem(spec_mod._FN_REGISTRY, "input.video",
                        lambda c, p: {"video": c.video_path})
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "detect.court", fake_court)
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "detect.pose", fake_pose)
    _stub_task_db(monkeypatch)

    g = _graph(
        [_node("n1", "input.video"), _node("n2", "detect.court"), _node("n3", "detect.pose")],
        [_edge("e1", "n1", "video", "n2", "video"),
         _edge("e2", "n1", "video", "n3", "video")],
    )
    result = _result("parallel_ok")
    Executor(g, AppConfig(), result).execute(tmp_path / "test.mp4")

    assert result.status == TaskStatus.SUCCEEDED
    statuses = {n.node_id: n.status for n in result.workflow_nodes}
    assert statuses == {"n1": "done", "n2": "done", "n3": "done"}
    assert result.elapsed_seconds < 5  # Barrier(2) 相会应瞬时完成


def test_fatal_failure_in_parallel_level(tmp_path, monkeypatch):
    """同层致命失败：收尾仍按 node.id 升序处理完全部节点（成功者标 done），随后任务 FAILED。"""
    assert spec_mod.get_spec("analyze.highlight").on_failure == "fail"
    assert spec_mod.get_spec("post.classify_strokes").on_failure == "fail"

    def boom(ctx, params):
        raise RuntimeError("boom")

    monkeypatch.setitem(spec_mod._FN_REGISTRY, "input.video",
                        lambda c, p: {"video": c.video_path})
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "analyze.highlight", boom)
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "post.classify_strokes",
                        lambda c, p: {})
    _stub_task_db(monkeypatch)

    g = _graph(
        [_node("n1", "input.video"), _node("n2", "analyze.highlight"),
         _node("n3", "post.classify_strokes")],
        [_edge("e1", "n1", "video", "n2", "video"),
         _edge("e2", "n1", "video", "n3", "video")],
    )
    result = _result("fatal_level")
    Executor(g, AppConfig(), result).execute(tmp_path / "test.mp4")

    assert result.status == TaskStatus.FAILED
    assert "n2" in result.error and "boom" in result.error
    statuses = {n.node_id: n.status for n in result.workflow_nodes}
    assert statuses["n2"] == "failed"
    assert statuses["n3"] == "done"  # 收尾不因同层致命失败中断，状态如实记录


def test_linear_graph_runs_inline(tmp_path, monkeypatch):
    """链式图每层单节点 → 内联执行，顺序与串行语义一致（零线程开销）。"""
    exec_order: list[str] = []

    def input_fn(c, p):
        exec_order.append("input.video")
        return {"video": c.video_path}

    def pre_fn(c, p):
        exec_order.append("preprocess.transcode")
        return {"video": c.video_path, "duration": 60.0}

    monkeypatch.setitem(spec_mod._FN_REGISTRY, "input.video", input_fn)
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "preprocess.transcode", pre_fn)
    _stub_task_db(monkeypatch)

    g = _graph(
        [_node("n1", "input.video"), _node("n2", "preprocess.transcode")],
        [_edge("e1", "n1", "video", "n2", "video")],
    )
    result = _result("inline_chain")
    Executor(g, AppConfig(), result).execute(tmp_path / "test.mp4")

    assert result.status == TaskStatus.SUCCEEDED
    assert exec_order == ["input.video", "preprocess.transcode"]


# ---------- 批次 C2：声明式落库 ----------

def test_persistence_declarations():
    """NodeSpec 声明：preprocess.records_input；edit/report.persists；preprocess 透出 meta 端口。"""
    pre = spec_mod.get_spec("preprocess.transcode")
    assert pre is not None
    assert pre.records_input is True
    assert pre.persists == ()
    assert ("meta", "meta") in [(p.name, p.type) for p in pre.outputs]

    edit = spec_mod.get_spec("edit.concat")
    assert edit.persists == (("video", "highlight_video"),)
    assert edit.records_input is False

    rep = spec_mod.get_spec("report.technical")
    assert rep.persists == (("report", "report"),)
    assert rep.records_input is False


@pytest.mark.parametrize("mod_name", ["preprocess_transcode", "edit_concat", "report_technical"])
def test_nodes_are_pure_no_db_service(mod_name):
    """批次 C2：节点模块不直调 db_service（落库由执行器声明式执行，节点保持纯函数）。"""
    import importlib
    mod = importlib.import_module(f"app.workflow.nodes.{mod_name}")
    assert not hasattr(mod, "db_service")


def test_record_input_declares_kwargs(tmp_path, monkeypatch):
    """_record_input：video_path/level 取 Context，duration 取端口，尺寸/fps 取 meta 端口。"""
    captured: list[dict] = []
    monkeypatch.setattr(db_service, "record_task_input", lambda **kw: captured.append(kw))

    ex = Executor(WorkflowGraph(), AppConfig(), SimpleNamespace(task_id="t1"))
    ctx = Context(video_path=tmp_path / "input.mp4", task_out=tmp_path,
                  task_id="t1", level="beginner")
    outputs = {"video": tmp_path / "v.mp4", "duration": 120.5,
               "meta": {"duration": 120.5, "width": 1280, "height": 720, "fps": 30.0}}

    ex._record_input(spec_mod.get_spec("preprocess.transcode"), outputs, ctx)
    assert len(captured) == 1
    kw = captured[0]
    assert kw["video_path"] == tmp_path / "input.mp4"
    assert kw["duration_seconds"] == 120.5
    assert kw["width"] == 1280 and kw["height"] == 720 and kw["fps"] == 30.0
    assert kw["level"] == "beginner"

    # 未声明 records_input 的节点不落输入元信息
    ex._record_input(spec_mod.get_spec("edit.concat"), outputs, ctx)
    assert len(captured) == 1


def test_record_outputs_path_and_convention(tmp_path, monkeypatch):
    """_record_outputs：路径端口直用；非路径值按约定 task_out/{kind}.json；文件缺失/None 跳过。"""
    captured: list[tuple] = []
    monkeypatch.setattr(db_service, "record_task_output",
                        lambda *a, **kw: captured.append((a, kw)))

    ex = Executor(WorkflowGraph(), AppConfig(), SimpleNamespace(task_id="t1"))
    ctx = Context(task_out=tmp_path, task_id="t1")

    # edit.concat：Path 端口值 → kind=highlight_video + target_duration
    video = tmp_path / "hl.mp4"
    video.write_bytes(b"0123456789")  # 10 bytes
    ex._record_outputs(spec_mod.get_spec("edit.concat"), {"video": video}, ctx)
    assert len(captured) == 1
    a, kw = captured[0]
    assert a[:3] == ("t1", "highlight_video", str(video))
    assert a[3] == 10 / (1024 * 1024)
    assert kw["target_duration"] == AppConfig().highlight.target_duration

    # report.technical：非路径值 + 约定文件不存在 → 跳过（等价原 exists 守卫）
    ex._record_outputs(spec_mod.get_spec("report.technical"), {"report": object()}, ctx)
    assert len(captured) == 1

    # 约定文件存在 → 记录，target_duration=None
    (tmp_path / "report.json").write_text("{}", encoding="utf-8")
    ex._record_outputs(spec_mod.get_spec("report.technical"), {"report": object()}, ctx)
    assert len(captured) == 2
    a, kw = captured[1]
    assert a[:3] == ("t1", "report", str(tmp_path / "report.json"))
    assert kw["target_duration"] is None

    # None 端口值 → 跳过
    ex._record_outputs(spec_mod.get_spec("edit.concat"), {"video": None}, ctx)
    assert len(captured) == 2


def test_executor_persists_end_to_end(tmp_path, monkeypatch):
    """端到端：执行器收尾阶段统一执行 records_input（preprocess）与 persists（edit）。"""
    video_out = tmp_path / "highlight.mp4"
    video_out.write_bytes(b"0123456789")  # 10 bytes

    hl = HighlightResult(segments=[Segment(start=0, end=5, label="ace", confidence=0.9)])
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "input.video",
                        lambda c, p: {"video": c.video_path})
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "preprocess.transcode",
                        lambda c, p: {"video": c.video_path, "duration": 60.0})
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "analyze.highlight",
                        lambda c, p: {"highlight": hl})
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "edit.concat",
                        lambda c, p: {"video": video_out})

    inputs: list[dict] = []
    outputs: list[tuple] = []
    _stub_task_db(monkeypatch, inputs=inputs, outputs=outputs)

    g = _graph(
        [_node("n1", "input.video"), _node("n2", "preprocess.transcode"),
         _node("n3", "analyze.highlight"), _node("n4", "edit.concat")],
        [_edge("e1", "n1", "video", "n2", "video"),
         _edge("e2", "n2", "video", "n3", "video"),
         _edge("e3", "n3", "highlight", "n4", "highlight"),
         _edge("e4", "n2", "video", "n4", "video")],
    )
    result = _result("persist_e2e")
    Executor(g, AppConfig(), result).execute(tmp_path / "test.mp4")

    assert result.status == TaskStatus.SUCCEEDED

    # records_input：preprocess 成功后执行器记录输入元信息（fake 无 meta → 尺寸 None）
    assert len(inputs) == 1
    assert inputs[0]["duration_seconds"] == 60.0
    assert inputs[0]["video_path"] == tmp_path / "test.mp4"
    assert inputs[0]["level"] == "intermediate"
    assert inputs[0]["width"] is None

    # persists：edit.concat 的 video 端口 → outputs kind=highlight_video
    assert len(outputs) == 1
    (a, kw) = outputs[0]
    assert a[:3] == ("persist_e2e", "highlight_video", str(video_out))
    assert a[3] == 10 / (1024 * 1024)
    assert kw["target_duration"] == AppConfig().highlight.target_duration
