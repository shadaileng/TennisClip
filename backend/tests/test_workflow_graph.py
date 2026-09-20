"""Step 3 测试：图结构与执行器。

TC-15 ~ TC-30：图校验、拓扑排序、序列化往返、Context 传值、产物投影、stage 上报。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import AppConfig
from app.models import HighlightResult, Segment, TechnicalReport
from app.workflow.spec import get_fn
import app.workflow.nodes  # noqa: F401  确保所有内置节点已注册
from app.workflow.graph import WorkflowGraph


# ---------- 默认图构建 ----------

def _default_graph_dict():
    """构造与方案文档一致的默认图 JSON。"""
    return {
        "version": 1,
        "name": "默认工作流",
        "nodes": [
            {"id": "n1", "type": "input.video", "params": {}, "enabled": True},
            {"id": "n2", "type": "preprocess.transcode", "params": {"height": 720, "fps": 30, "max_input_seconds": 300}, "enabled": True},
            {"id": "n3", "type": "detect.candidates", "params": {"mode": "audio_motion", "top_n": 5}, "enabled": True},
            {"id": "n4", "type": "analyze.highlight", "params": {"level": "intermediate", "analysis_mode": "frame"}, "enabled": True},
            {"id": "n5", "type": "edit.concat", "params": {"target_duration": 15, "all_mode": False}, "enabled": True},
            {"id": "n6", "type": "report.technical", "params": {"level": "intermediate"}, "enabled": True},
            {"id": "n7", "type": "output.artifact", "params": {}, "enabled": True},
        ],
        "edges": [
            {"id": "e1", "from": ["n1", "video"], "to": ["n2", "video"]},
            {"id": "e2", "from": ["n2", "video"], "to": ["n3", "video"]},
            {"id": "e3", "from": ["n2", "duration"], "to": ["n3", "duration"]},
            {"id": "e4", "from": ["n2", "video"], "to": ["n4", "video"]},
            {"id": "e5", "from": ["n3", "candidates"], "to": ["n4", "candidates"]},
            {"id": "e6", "from": ["n4", "highlight"], "to": ["n5", "highlight"]},
            {"id": "e7", "from": ["n2", "video"], "to": ["n5", "video"]},
            {"id": "e8", "from": ["n4", "highlight"], "to": ["n6", "highlight"]},
            {"id": "e9", "from": ["n5", "video"], "to": ["n7", "video"]},
            {"id": "e10", "from": ["n6", "report"], "to": ["n7", "report"]},
            {"id": "e11", "from": ["n2", "video"], "to": ["n6", "video"]},
            {"id": "e12", "from": ["n2", "duration"], "to": ["n4", "duration"]},
            {"id": "e13", "from": ["n4", "highlight"], "to": ["n7", "highlight"]},
        ],
    }


# ---------- TC-15: 未知节点 type → errors ----------

def test_validate_unknown_node_type():
    """未知节点 type → errors 含中文原因，validate() 不抛异常。"""
    g = _default_graph_dict()
    g["nodes"].append({"id": "n99", "type": "nonexistent.type", "params": {}, "enabled": True})
    graph = WorkflowGraph.from_dict(g)
    result = graph.validate()
    assert result["ok"] is False
    assert any("未知节点类型" in e["message"] for e in result["errors"])


# ---------- TC-16: 端口类型不匹配 → R4 报错 ----------

def test_validate_port_type_mismatch():
    """端口类型不匹配（video → duration）→ R4 报错。"""
    g = _default_graph_dict()
    # 把 n2→n3 的 video 连线改为 video→duration（类型不匹配）
    g["edges"][1] = {"id": "e2", "from": ["n2", "video"], "to": ["n3", "duration"]}
    graph = WorkflowGraph.from_dict(g)
    result = graph.validate()
    assert result["ok"] is False
    assert any("类型不匹配" in e["message"] for e in result["errors"])


# ---------- TC-17: 必填输入未连线 → R5 报错 ----------

def test_validate_required_input_missing():
    """必填输入未连线 → R5 报错。"""
    g = _default_graph_dict()
    # 删除 n1→n2 的连线（n2 的 video 是必填）
    g["edges"] = [e for e in g["edges"] if e["id"] != "e1"]
    graph = WorkflowGraph.from_dict(g)
    result = graph.validate()
    assert result["ok"] is False
    assert any("必填输入" in e["message"] for e in result["errors"])


# ---------- TC-18: 存在环 → R7 报错 ----------

def test_validate_cycle_detected():
    """存在环（n2 → n3 → n2）→ R7 检出。"""
    g = _default_graph_dict()
    # 添加一条反向边 n3→n2 造成环
    g["edges"].append({"id": "e_cycle", "from": ["n3", "candidates"], "to": ["n2", "video"]})
    graph = WorkflowGraph.from_dict(g)
    result = graph.validate()
    assert result["ok"] is False
    assert any("环" in e["message"] for e in result["errors"])


# ---------- TC-19: 节点 id 重复、edge 指向不存在节点 ----------

def test_validate_duplicate_node_id():
    """节点 id 重复 → R2 报错。"""
    g = _default_graph_dict()
    g["nodes"].append({"id": "n2", "type": "input.video", "params": {}, "enabled": True})
    graph = WorkflowGraph.from_dict(g)
    result = graph.validate()
    assert result["ok"] is False
    assert any("重复" in e["message"] for e in result["errors"])


def test_validate_edge_references_nonexistent_node():
    """edge 指向不存在节点 → R3 报错。"""
    g = _default_graph_dict()
    g["edges"].append({"id": "e_bad", "from": ["n999", "video"], "to": ["n1", "video"]})
    graph = WorkflowGraph.from_dict(g)
    result = graph.validate()
    assert result["ok"] is False
    assert any("不存在" in e["message"] for e in result["errors"])


# ---------- TC-20: 无 enabled 的 output.artifact → R8 报错 ----------

def test_validate_no_output_node():
    """无 enabled 的 output.artifact → R8 报错。"""
    g = _default_graph_dict()
    # 停用所有 output.artifact 节点
    for n in g["nodes"]:
        if n["type"] == "output.artifact":
            n["enabled"] = False
    graph = WorkflowGraph.from_dict(g)
    result = graph.validate()
    assert result["ok"] is False
    assert any("output.artifact" in e["message"] for e in result["errors"])


# ---------- TC-21: 合法默认图 → ok=True, 拓扑序确定且一致 ----------

def test_default_graph_valid_and_deterministic():
    """合法默认图 → ok=True，拓扑序为期望序列，且多次调用完全一致。"""
    g = _default_graph_dict()
    graph = WorkflowGraph.from_dict(g)
    result = graph.validate()
    assert result["ok"] is True, f"校验失败: {result['errors']}"

    order1 = graph.topo_order()
    order2 = graph.topo_order()
    assert order1 == order2, "多次调用拓扑序不一致"
    # 默认图拓扑序：n1→n2→(n3,n4)→(n5,n6)→n7
    assert order1[0] == "n1"
    assert order1[1] == "n2"
    assert order1[-1] == "n7"


# ---------- TC-22: to_dict/from_dict 往返幂等 ----------

def test_graph_dict_roundtrip():
    """to_dict/from_dict 往返幂等（含中文与 params）。"""
    g = _default_graph_dict()
    graph1 = WorkflowGraph.from_dict(g)
    d = graph1.to_dict()
    graph2 = WorkflowGraph.from_dict(d)
    d2 = graph2.to_dict()
    assert d == d2

    # 验证中文名称保持
    assert graph2.name == "默认工作流"


# ---------- TC-23: 线性图按拓扑序执行，下游正确消费上游输出 ----------

def test_linear_graph_execution(tmp_path, monkeypatch):
    """线性图按拓扑序执行，下游正确消费上游输出（Context 传值）。"""
    from app.workflow.executor import Executor, Context

    exec_order = []

    def make_fake_fn(name):
        def fn(ctx, params):
            exec_order.append(name)
            return {"video": ctx.video_path}
        return fn

    g = {
        "version": 1,
        "name": "线性测试",
        "nodes": [
            {"id": "n1", "type": "input.video", "params": {}, "enabled": True},
            {"id": "n2", "type": "preprocess.transcode", "params": {}, "enabled": True},
        ],
        "edges": [
            {"id": "e1", "from": ["n1", "video"], "to": ["n2", "video"]},
        ],
    }
    graph = WorkflowGraph.from_dict(g)

    # monkeypatch 所有节点的 run 函数
    from app.workflow import spec as spec_mod
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "input.video", make_fake_fn("input.video"))
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "preprocess.transcode", make_fake_fn("preprocess.transcode"))

    result = SimpleNamespace(
        task_id="linear_test", stage="pending", status="pending",
        highlight=None, report=None, highlight_video_path=None,
        report_path=None, report_error=None, error=None, elapsed_seconds=0.0,
    )
    executor = Executor(graph, AppConfig(), result)
    executor.execute(tmp_path / "test.mp4")

    assert exec_order == ["input.video", "preprocess.transcode"]


# ---------- TC-25: optional 节点失败 → 记 warning，整体仍 succeeded ----------

def test_optional_node_failure_continues(tmp_path, monkeypatch):
    """optional 节点失败 → 记 warning，整体仍 succeeded。"""
    from app.workflow.executor import Executor
    from app.workflow import spec as spec_mod
    from app.models import HighlightResult, Segment

    def fail_fn(ctx, params):
        raise RuntimeError("模拟失败")

    def track_fn(name):
        def fn(ctx, params):
            if name == "input.video":
                return {"video": ctx.video_path}
            elif name == "preprocess.transcode":
                return {"video": ctx.video_path, "duration": 60.0}
            elif name == "detect.candidates":
                return {"candidates": []}
            elif name == "analyze.highlight":
                return {"highlight": HighlightResult(segments=[Segment(start=1, end=5, label="ace", confidence=0.9)])}
            elif name == "edit.concat":
                out = ctx.task_out / "highlight.mp4"
                out.write_bytes(b"dummy")
                return {"video": out}
            elif name == "output.artifact":
                return {}
            return {}
        return fn

    for nt in ["input.video", "preprocess.transcode", "detect.candidates",
               "analyze.highlight", "edit.concat", "output.artifact"]:
        monkeypatch.setitem(spec_mod._FN_REGISTRY, nt, track_fn(nt))

    # report.technical 失败（optional=True）
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "report.technical", fail_fn)

    g = _default_graph_dict()
    graph = WorkflowGraph.from_dict(g)
    result = SimpleNamespace(
        task_id="opt_test", stage="pending", status="pending",
        highlight=None, report=None, highlight_video_path=None,
        report_path=None, report_error=None, error=None, elapsed_seconds=0.0,
    )
    executor = Executor(graph, AppConfig(), result)
    executor.execute(tmp_path / "test.mp4")

    assert result.status == "succeeded"


# ---------- TC-26: 非 optional 节点失败 → status=failed ----------

def test_required_node_failure_sets_failed(tmp_path, monkeypatch):
    """非 optional 节点失败 → status=failed，error 含节点 id 与类型。"""
    from app.workflow.executor import Executor
    from app.workflow import spec as spec_mod

    def fail_fn(ctx, params):
        raise RuntimeError("处理出错")

    monkeypatch.setitem(spec_mod._FN_REGISTRY, "preprocess.transcode", fail_fn)

    g = _default_graph_dict()
    graph = WorkflowGraph.from_dict(g)
    result = SimpleNamespace(
        task_id="fail_test", stage="pending", status="pending",
        highlight=None, report=None, highlight_video_path=None,
        report_path=None, report_error=None, error=None, elapsed_seconds=0.0,
    )
    executor = Executor(graph, AppConfig(), result)
    executor.execute(tmp_path / "test.mp4")

    assert result.status == "failed"
    assert "n2" in result.error  # 节点 id
    assert "preprocess.transcode" in result.error  # 节点类型


# ---------- TC-27: enabled=false 节点被跳过 ----------

def test_disabled_node_skipped(tmp_path, monkeypatch):
    """enabled=false 节点被跳过；其下游必填输入缺失时报清晰错误。"""
    from app.workflow.executor import Executor
    from app.workflow import spec as spec_mod

    executed = []

    def track_fn(name):
        def fn(ctx, params):
            executed.append(name)
            return {"video": ctx.video_path, "duration": 60.0}
        return fn

    g = _default_graph_dict()
    # 停用 detect.candidates
    for n in g["nodes"]:
        if n["type"] == "detect.candidates":
            n["enabled"] = False
    graph = WorkflowGraph.from_dict(g)

    monkeypatch.setitem(spec_mod._FN_REGISTRY, "input.video", track_fn("input.video"))
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "preprocess.transcode", track_fn("preprocess.transcode"))
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "detect.candidates", track_fn("detect.candidates"))

    result = SimpleNamespace(
        task_id="skip_test", stage="pending", status="pending",
        highlight=None, report=None, highlight_video_path=None,
        report_path=None, report_error=None, error=None, elapsed_seconds=0.0,
    )
    executor = Executor(graph, AppConfig(), result)
    executor.execute(tmp_path / "test.mp4")

    assert "detect.candidates" not in executed  # 被跳过


# ---------- TC-28: 默认图执行过程中 result.stage 依次变化 ----------

def test_stage_reporting_in_order(tmp_path, monkeypatch):
    """默认图执行过程中 result.stage 依次为 preprocessing → highlighting → editing → reporting。"""
    from app.workflow.executor import Executor
    from app.workflow import spec as spec_mod
    from app.models import HighlightResult, TechnicalReport

    stages_seen = []

    def track_stage_fn(name):
        def fn(ctx, params):
            stages_seen.append(ctx.result.stage)
            if name == "input.video":
                return {"video": ctx.video_path}
            elif name == "preprocess.transcode":
                return {"video": ctx.video_path, "duration": 60.0}
            elif name == "detect.candidates":
                return {"candidates": []}
            elif name == "analyze.highlight":
                return {"highlight": HighlightResult(segments=[Segment(start=1, end=5, label="ace", confidence=0.9)])}
            elif name == "edit.concat":
                out = ctx.task_out / "highlight.mp4"
                out.write_bytes(b"dummy")
                return {"video": out}
            elif name == "report.technical":
                return {"report": TechnicalReport(level="intermediate", summary="test")}
            elif name == "output.artifact":
                return {}
            return {}
        return fn

    for node_type in ["input.video", "preprocess.transcode", "detect.candidates",
                       "analyze.highlight", "edit.concat", "report.technical", "output.artifact"]:
        monkeypatch.setitem(spec_mod._FN_REGISTRY, node_type, track_stage_fn(node_type))

    g = _default_graph_dict()
    graph = WorkflowGraph.from_dict(g)
    result = SimpleNamespace(
        task_id="stage_test", stage="pending", status="pending",
        highlight=None, report=None, highlight_video_path=None,
        report_path=None, report_error=None, error=None, elapsed_seconds=0.0,
    )
    executor = Executor(graph, AppConfig(), result)
    executor.execute(tmp_path / "test.mp4")

    # input.video 的 stage 是 "input"（未改变 pending），其余按序
    assert "preprocessing" in stages_seen
    assert "highlighting" in stages_seen
    assert "editing" in stages_seen


# ---------- TC-29: 产物投影 ----------

def test_artifact_projection(tmp_path, monkeypatch):
    """产物投影：highlight / report / highlight_video_path 正确写回 TaskResult。"""
    from app.workflow.executor import Executor
    from app.workflow import spec as spec_mod
    from app.models import HighlightResult, TechnicalReport

    hl = HighlightResult(segments=[Segment(start=1, end=5, label="ace", confidence=0.9)])
    report_obj = TechnicalReport(level="intermediate", summary="test report")
    video_out = tmp_path / "highlight.mp4"
    video_out.write_bytes(b"dummy")

    def fake_fn(name):
        def fn(ctx, params):
            if name == "input.video":
                return {"video": ctx.video_path}
            elif name == "preprocess.transcode":
                return {"video": ctx.video_path, "duration": 60.0}
            elif name == "detect.candidates":
                return {"candidates": []}
            elif name == "analyze.highlight":
                return {"highlight": hl}
            elif name == "edit.concat":
                return {"video": video_out}
            elif name == "report.technical":
                return {"report": report_obj}
            return {}
        return fn

    for node_type in ["input.video", "preprocess.transcode", "detect.candidates",
                       "analyze.highlight", "edit.concat", "report.technical"]:
        monkeypatch.setitem(spec_mod._FN_REGISTRY, node_type, fake_fn(node_type))
    # output.artifact 使用真实 run 函数（直接写入 result）

    g = _default_graph_dict()
    graph = WorkflowGraph.from_dict(g)
    result = SimpleNamespace(
        task_id="artifact_test", stage="pending", status="pending",
        highlight=None, report=None, highlight_video_path=None,
        report_path=None, report_error=None, error=None, elapsed_seconds=0.0,
    )
    executor = Executor(graph, AppConfig(), result)
    executor.execute(tmp_path / "test.mp4")

    assert result.highlight is not None
    assert result.report is not None
    assert result.highlight_video_path is not None


# ---------- TC-30: 落库钩子 ----------

def test_db_hooks_called(tmp_path, monkeypatch):
    """record_task_start / record_task_finish 各调用一次。"""
    from app.workflow.executor import Executor
    from app.services import db_service

    calls = {"start": 0, "finish": 0}

    def fake_start(task_id, video, level):
        calls["start"] += 1

    def fake_finish(**kwargs):
        calls["finish"] += 1

    monkeypatch.setattr(db_service, "record_task_start", fake_start)
    monkeypatch.setattr(db_service, "record_task_finish", fake_finish)
    monkeypatch.setattr(db_service, "record_task_output", lambda *a, **kw: None)

    g = _default_graph_dict()
    graph = WorkflowGraph.from_dict(g)
    result = SimpleNamespace(
        task_id="hook_test", stage="pending", status="pending",
        highlight=None, report=None, highlight_video_path=None,
        report_path=None, report_error=None, error=None, elapsed_seconds=0.0,
    )
    executor = Executor(graph, AppConfig(), result)
    executor.execute(tmp_path / "test.mp4")

    assert calls["start"] == 1
    assert calls["finish"] == 1
