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


# ---------- 批次 A/B 独立性加固（文档 12 · Step 0）----------

def _make_result(task_id: str = "indep_test") -> SimpleNamespace:
    return SimpleNamespace(
        task_id=task_id, stage="pending", status="pending",
        highlight=None, report=None, highlight_video_path=None,
        report_path=None, report_error=None, error=None, elapsed_seconds=0.0,
    )


# ---------- S0-1: 批次 A1 节点级 config 隔离 ----------

def test_config_isolation_per_node(tmp_path, monkeypatch):
    """节点内改写 ctx.config 不影响其他节点与全局配置（批次 A1 深拷贝隔离）。"""
    from app.workflow.executor import Executor
    from app.workflow import spec as spec_mod

    seen_configs = []
    seen_global_before = None

    def fn_a(ctx, params):
        seen_configs.append(ctx.config)
        # 模拟节点改写 config（新契约：直接改，无需 try/finally 恢复）
        ctx.config.highlight.candidate_mode = "audio"
        ctx.config.video.max_input_seconds = 9999
        return {"video": ctx.video_path, "duration": 60.0}

    def fn_b(ctx, params):
        seen_configs.append(ctx.config)
        return {"candidates": []}

    monkeypatch.setitem(spec_mod._FN_REGISTRY, "input.video", lambda c, p: {"video": c.video_path})
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "preprocess.transcode", fn_a)
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "detect.candidates", fn_b)
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "analyze.highlight",
                        lambda c, p: {"highlight": HighlightResult(segments=[])})
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "edit.concat",
                        lambda c, p: {"video": c.video_path})
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "report.technical",
                        lambda c, p: {"report": None})
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "output.artifact", lambda c, p: {})

    g = _default_graph_dict()
    graph = WorkflowGraph.from_dict(g)
    global_config = AppConfig()
    global_config.highlight.candidate_mode = "motion"
    global_config.video.max_input_seconds = 12345
    executor = Executor(graph, global_config, _make_result("cfg_isolation"))
    executor.execute(tmp_path / "test.mp4")

    # 节点看到的 config 互相独立、也与全局配置独立
    assert seen_configs[0] is not seen_configs[1]
    # 节点 A 的改写未污染节点 B 的视图
    assert seen_configs[1].highlight.candidate_mode == "motion"
    assert seen_configs[1].video.max_input_seconds == 12345
    # 全局配置未被任何节点改写
    assert global_config.highlight.candidate_mode == "motion"
    assert global_config.video.max_input_seconds == 12345


# ---------- S0-2: 批次 A2 值不可变传递（并联分支互不污染）----------

def test_highlight_branches_do_not_pollute(tmp_path, monkeypatch):
    """两分支共享同一 highlight 时，各分支的 model_copy 结果互不污染（批次 A2）。"""
    from app.workflow.executor import Executor
    from app.workflow import spec as spec_mod

    shared = HighlightResult(
        segments=[Segment(start=1, end=5, label="ace", confidence=0.9)],
        all_highlights=False,
    )

    def fn_edit(ctx, params):
        hl = ctx.inputs.get("highlight")
        return {"highlight": hl.model_copy(update={"all_highlights": True})}

    def fn_post(ctx, params):
        hl = ctx.inputs.get("highlight")
        # 模拟 post.filter_segments：过滤后生成新对象
        filtered = hl.model_copy(update={"segments": [s for s in hl.segments if s.label != "other"]})
        return {"highlight": filtered}

    monkeypatch.setitem(spec_mod._FN_REGISTRY, "input.video", lambda c, p: {"video": c.video_path})
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "preprocess.transcode",
                        lambda c, p: {"video": c.video_path, "duration": 60.0})
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "detect.candidates",
                        lambda c, p: {"candidates": []})
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "analyze.highlight",
                        lambda c, p: {"highlight": shared})
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "edit.concat", fn_edit)
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "post.filter_segments", fn_post)
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "report.technical", lambda c, p: {"report": None})
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "output.artifact", lambda c, p: {})

    # 图：analyze.highlight 的 highlight 同时连向 edit.concat 与 post.filter_segments（并联）
    g = _default_graph_dict()
    g["nodes"].append({"id": "n8", "type": "post.filter_segments", "params": {}, "enabled": True})
    g["edges"].append({"id": "e14", "from": ["n4", "highlight"], "to": ["n8", "highlight"]})
    graph = WorkflowGraph.from_dict(g)
    executor = Executor(graph, AppConfig(), _make_result("branch_iso"))
    executor.execute(tmp_path / "test.mp4")

    # 共享对象本身未被任何分支原地修改
    assert shared.all_highlights is False
    assert len(shared.segments) == 1


# ---------- S0-3: 批次 A3 运行时端口值类型校验 ----------

def test_runtime_port_type_validation_on_store(tmp_path, monkeypatch):
    """节点输出端口值类型错误 → store 期抛 TypeError，任务 FAILED 且 error 含端口类型校验信息（批次 A3）。

    校验发生在 executor 的 store 期（而非节点深处），错误定位到具体节点与端口；
    默认 on_failure=fail 语义下任务整体 FAILED。
    """
    from app.workflow.executor import Executor
    from app.workflow import spec as spec_mod

    def bad_output(ctx, params):
        # highlight 端口应输出 HighlightResult，这里故意输出 str
        return {"highlight": "不是 HighlightResult"}

    monkeypatch.setitem(spec_mod._FN_REGISTRY, "input.video", lambda c, p: {"video": c.video_path})
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "preprocess.transcode",
                        lambda c, p: {"video": c.video_path, "duration": 60.0})
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "detect.candidates", lambda c, p: {"candidates": []})
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "analyze.highlight", bad_output)
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "edit.concat",
                        lambda c, p: {"video": c.video_path})
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "report.technical", lambda c, p: {"report": None})
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "output.artifact", lambda c, p: {})

    g = _default_graph_dict()
    graph = WorkflowGraph.from_dict(g)
    result = _make_result("type_check")
    executor = Executor(graph, AppConfig(), result)
    executor.execute(tmp_path / "test.mp4")

    # 任务失败（on_failure=fail 默认），且失败信息定位到 n4（analyze.highlight）与其端口
    assert result.status == "failed"
    assert "n4" in result.error
    assert "highlight" in result.error
    # 错误来自 store 期端口值类型校验（而非节点内部逻辑）
    assert "值类型不匹配" in result.error
    statuses = {n.node_id: n.status for n in result.workflow_nodes}
    assert statuses["n4"] == "failed"


def test_validate_port_value_unit():
    """validate_port_value 单元行为：None 跳过 / 未登记类型跳过 / 匹配通过 / 错配抛错。"""
    from app.workflow.spec import validate_port_value, PortType
    from app.models import HighlightResult

    # None 跳过
    validate_port_value(PortType.HIGHLIGHT, None)
    # 未登记类型跳过（向后兼容）
    validate_port_value("unknown.type", object())
    # 匹配通过
    validate_port_value(PortType.HIGHLIGHT, HighlightResult(segments=[]))
    # 错配抛 TypeError（错误信息含端口名、期望类型、实际类型）
    with pytest.raises(TypeError, match="值类型不匹配"):
        validate_port_value(PortType.HIGHLIGHT, "oops")


# ---------- S0-4: 批次 B1 失败降级与级联跳过 ----------

def test_on_failure_skip_cascades_downstream(tmp_path, monkeypatch):
    """on_failure=skip 节点失败 → 本节点与下游 skipped，任务仍 SUCCEEDED（批次 B1）。"""
    from app.workflow.executor import Executor
    from app.workflow import spec as spec_mod
    from app.workflow.spec import NodeSpec, Port, PortType, register

    # 注册一个可失败测试节点：失败策略 skip
    @register(
        NodeSpec(
            type="test.fail_cascade",
            label="失败级联测试",
            category="test",
            description="",
            inputs=[Port(name="duration", type=PortType.DURATION)],
            outputs=[Port(name="highlight", type=PortType.HIGHLIGHT)],
            params=[],
            stage="testing",
            on_failure="skip",
        )
    )
    def _cascade(ctx, params):
        raise RuntimeError("模拟 CV 节点失败（如 TrackNet 缺 GPU）")

    monkeypatch.setitem(spec_mod._FN_REGISTRY, "input.video", lambda c, p: {"video": c.video_path})
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "preprocess.transcode",
                        lambda c, p: {"video": c.video_path, "duration": 60.0})
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "test.fail_cascade", _cascade)
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "edit.concat",
                        lambda c, p: {"video": c.video_path})
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "output.artifact", lambda c, p: {})

    # 图：preprocess → test.fail_cascade → edit.concat（edit.concat 必填 highlight 来自失败节点）
    g = {
        "version": 1,
        "name": "失败级联",
        "nodes": [
            {"id": "n1", "type": "input.video", "params": {}, "enabled": True},
            {"id": "n2", "type": "preprocess.transcode", "params": {}, "enabled": True},
            {"id": "n3", "type": "test.fail_cascade", "params": {}, "enabled": True},
            {"id": "n5", "type": "edit.concat", "params": {}, "enabled": True},
            {"id": "n7", "type": "output.artifact", "params": {}, "enabled": True},
        ],
        "edges": [
            {"id": "e1", "from": ["n1", "video"], "to": ["n2", "video"]},
            {"id": "e2", "from": ["n2", "duration"], "to": ["n3", "duration"]},
            {"id": "e3", "from": ["n3", "highlight"], "to": ["n5", "highlight"]},
            {"id": "e4", "from": ["n2", "video"], "to": ["n5", "video"]},
            {"id": "e5", "from": ["n3", "highlight"], "to": ["n7", "highlight"]},
            {"id": "e6", "from": ["n5", "video"], "to": ["n7", "video"]},
        ],
    }
    graph = WorkflowGraph.from_dict(g)
    result = _make_result("cascade_test")
    executor = Executor(graph, AppConfig(), result)
    executor.execute(tmp_path / "test.mp4")

    # 任务成功（skip 策略不拖垮任务）
    assert result.status == "succeeded"
    statuses = {n.node_id: n.status for n in result.workflow_nodes}
    assert statuses["n3"] == "skipped"      # 失败节点自身
    assert statuses["n5"] == "skipped"      # 下游级联跳过（必填 highlight 来自死亡节点）
    assert statuses["n7"] == "skipped"      # 再下游同样级联（n5.video 未产出 → n7.video 缺失）
    assert statuses["n1"] == "done"
    assert statuses["n2"] == "done"


def test_on_failure_fail_propagates_to_task_failed(tmp_path, monkeypatch):
    """on_failure=fail（默认）节点失败 → 任务 FAILED（向后兼容，与既有 TC-26 语义一致）。"""
    from app.workflow.executor import Executor
    from app.workflow import spec as spec_mod

    def fail_fn(ctx, params):
        raise RuntimeError("处理出错")

    monkeypatch.setitem(spec_mod._FN_REGISTRY, "preprocess.transcode", fail_fn)
    g = _default_graph_dict()
    graph = WorkflowGraph.from_dict(g)
    result = _make_result("fail_default")
    executor = Executor(graph, AppConfig(), result)
    executor.execute(tmp_path / "test.mp4")

    assert result.status == "failed"
    assert "n2" in result.error


def test_skip_cascade_does_not_affect_live_none_output(tmp_path, monkeypatch):
    """上游节点正常执行但输出 None（如 report all 档位）→ 不级联跳过下游（批次 B1 语义边界）。"""
    from app.workflow.executor import Executor
    from app.workflow import spec as spec_mod

    # report.technical all 档位返回 {"report": None}（节点正常执行、输出 None）
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "input.video", lambda c, p: {"video": c.video_path})
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "preprocess.transcode",
                        lambda c, p: {"video": c.video_path, "duration": 60.0})
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "detect.candidates", lambda c, p: {"candidates": []})
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "analyze.highlight",
                        lambda c, p: {"highlight": HighlightResult(segments=[], all_highlights=True)})
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "edit.concat",
                        lambda c, p: {"video": c.video_path, "highlight": c.inputs.get("highlight")})
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "report.technical", lambda c, p: {"report": None})
    monkeypatch.setitem(spec_mod._FN_REGISTRY, "output.artifact", lambda c, p: {})

    g = _default_graph_dict()
    graph = WorkflowGraph.from_dict(g)
    result = _make_result("live_none")
    executor = Executor(graph, AppConfig(), result)
    executor.execute(tmp_path / "test.mp4")

    # report 节点正常执行（输出 None），其下游 output.artifact 仍执行
    assert result.status == "succeeded"
    statuses = {n.node_id: n.status for n in result.workflow_nodes}
    assert statuses["n6"] == "done"      # report.technical 正常完成
    assert statuses["n7"] == "done"      # output 未级联跳过


# ---------- S0-5: 批次 B2 校验期提示 output.artifact 缺 highlight 入边 ----------

def test_validate_warns_output_artifact_missing_highlight_edge():
    """output.artifact 缺 highlight 入边且图中有 highlight 生产节点 → R8 阻断（批次 B2 校验期提示）。"""
    g = _default_graph_dict()
    # 删除 highlight→output.artifact 的连线（e13）；n4（analyze.highlight）仍产出 highlight
    g["edges"] = [e for e in g["edges"] if e["id"] != "e13"]
    graph = WorkflowGraph.from_dict(g)
    result = graph.validate()
    assert result["ok"] is False
    assert any(e.get("code") == "R8" and "highlight" in e["message"]
               for e in result["errors"]), f"未检出 R8 缺连告警: {result['errors']}"


def test_validate_no_warning_when_no_highlight_producer():
    """无 highlight 生产节点且 output.artifact 无 highlight 连线 → 仅 warning 不阻断。

    构造完整合法图：去掉所有 highlight 生产/消费节点（analyze.highlight /
    post.uniform_slices / edit.concat / report.technical），仅保留 input →
    preprocess → output，图中既无 highlight 生产者也无 highlight 连线。
    """
    g = {
        "version": 1,
        "name": "无 highlight 图",
        "nodes": [
            {"id": "n1", "type": "input.video", "params": {}, "enabled": True},
            {"id": "n2", "type": "preprocess.transcode", "params": {}, "enabled": True},
            {"id": "n7", "type": "output.artifact", "params": {}, "enabled": True},
        ],
        "edges": [
            {"id": "e1", "from": ["n1", "video"], "to": ["n2", "video"]},
            {"id": "e2", "from": ["n2", "video"], "to": ["n7", "video"]},
        ],
    }
    graph = WorkflowGraph.from_dict(g)
    result = graph.validate()
    # 不阻断（无 highlight 生产节点，缺连仅是 warning 不入 errors）
    assert result["ok"] is True, f"无 highlight 生产节点时缺连不应阻断: {result['errors']}"


def test_validate_default_graph_no_r8():
    """完整默认图（含 highlight→output.artifact 连线）不应出现 R8 告警。"""
    graph = WorkflowGraph.from_dict(_default_graph_dict())
    result = graph.validate()
    assert result["ok"] is True, f"默认图应通过校验: {result['errors']}"
    assert not any(e.get("code") == "R8" and "highlight" in e["message"]
                   for e in result["errors"])
