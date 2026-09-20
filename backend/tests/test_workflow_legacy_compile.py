"""Step 4 · 简易模式编译 + core.run_pipeline 重构 — TC-31~TC-37。"""

from __future__ import annotations

import pytest
import app.workflow.nodes  # noqa: F401 — 触发注册

from app.workflow.presets import compile_from_legacy, compile_from_config
from app.workflow.graph import WorkflowGraph


# ──────────────────────── helpers ────────────────────────


# ──────────── TC-31 ────────────
class TestTC31DefaultGraphAllStages:
    """compile_from_legacy() 返回的图：全四阶段、所有必填输入有来源。"""

    def test_stages_present(self):
        graph = compile_from_legacy()
        assert graph.frozen is True
        # 图中应有所有四个阶段的关键节点
        node_types = {n.type for n in graph.nodes.values()}
        assert "preprocess.transcode" in node_types
        assert "detect.candidates" in node_types
        assert "analyze.highlight" in node_types
        assert "edit.concat" in node_types
        assert "report.technical" in node_types

    def test_has_input_and_output(self):
        graph = compile_from_legacy()
        node_types = {n.type for n in graph.nodes.values()}
        assert "input.video" in node_types
        assert "output.artifact" in node_types

    def test_valid(self):
        graph = compile_from_legacy()
        result = graph.validate()
        assert result["ok"], f"默认图校验失败: {result['errors']}"

    def test_has_edges(self):
        graph = compile_from_legacy()
        assert len(graph.edges) > 0


# ──────────── TC-32 ────────────
class TestTC32PresetWithHighlight:
    """level=all 时：无 post.filter_segments 节点。"""

    def test_no_post_filter(self):
        graph = compile_from_legacy(level="all")
        node_types = {n.type for n in graph.nodes.values()}
        assert "post.filter_segments" not in node_types

    def test_post_uniform_still_present(self):
        graph = compile_from_legacy(level="all")
        node_types = {n.type for n in graph.nodes.values()}
        assert "post.uniform_slices" in node_types

    def test_valid(self):
        graph = compile_from_legacy(level="all")
        result = graph.validate()
        assert result["ok"], f"校验失败: {result['errors']}"


# ──────────── TC-33 ────────────
class TestTC33PresetWithoutPreprocess:
    """preprocess 关停：无 preprocess.transcode 节点。

    注意：highlight 阶段的 detect/analyze/uniform 节点需要 duration 输入，
    而 duration 仅由 preprocess 节点产出。因此当 preprocess 关停时，
    这些节点的必填 duration 输入无法连接，图校验会报 R5/R9 错误。
    这与 core.run_pipeline 中 _validate_enabled_stages 的语义一致
    （highlight/edit/report 依赖 preprocess）。
    """

    def test_no_preprocess_node(self):
        graph = compile_from_legacy(stages=["highlight", "edit", "report"])
        node_types = {n.type for n in graph.nodes.values()}
        assert "preprocess.transcode" not in node_types

    def test_has_highlight_nodes(self):
        graph = compile_from_legacy(stages=["highlight", "edit", "report"])
        node_types = {n.type for n in graph.nodes.values()}
        assert "detect.candidates" in node_types
        assert "analyze.highlight" in node_types


# ──────────── TC-34 ────────────
class TestTC34PresetFrozen:
    """compile_from_legacy 返回的图不可编辑。"""

    def test_frozen_flag(self):
        graph = compile_from_legacy()
        assert graph.frozen is True

    def test_cannot_add_node(self):
        graph = compile_from_legacy()
        with pytest.raises(RuntimeError, match="锁定"):
            graph.add_node("z1", "input.video", {})

    def test_cannot_add_edge(self):
        graph = compile_from_legacy()
        ids = list(graph.nodes.keys())
        with pytest.raises(RuntimeError, match="锁定"):
            graph.add_edge(ids[0], "video", ids[1], "video")


# ──────────── TC-35 ────────────
class TestTC35EquivalentExecution:
    """compile_from_legacy 生成的图与旧 run_pipeline 逻辑等价（阶段顺序一致）。"""

    def test_topo_order_contains_stages(self):
        graph = compile_from_legacy()
        order = graph.topo_order()
        # 顺序：preprocess → detect → analyze → post_filter → post_uniform → edit → report
        indices = {nid: i for i, nid in enumerate(order)}
        assert indices["n2"] < indices["n3"] < indices["n4"]  # preprocess < detect < analyze
        assert indices["n4"] < indices["n7"]  # analyze < edit
        assert indices["n7"] < indices["n8"]  # edit < report


# ──────────── TC-36 ────────────
class TestTC36LegacyCompileWithStrategy:
    """compile_from_legacy(strategy='video') 传播到 analyze.highlight 的 analysis_mode。"""

    def test_strategy_in_analyze(self):
        graph = compile_from_legacy(strategy="video")
        analyze = graph.nodes["n4"]
        assert analyze.params.get("analysis_mode") == "video"

    def test_strategy_default_frame(self):
        graph = compile_from_legacy(strategy="frame")
        analyze = graph.nodes["n4"]
        assert analyze.params.get("analysis_mode") == "frame"

    def test_valid(self):
        graph = compile_from_legacy(strategy="video")
        result = graph.validate()
        assert result["ok"], f"校验失败: {result['errors']}"


# ──────────── TC-37 ────────────
class TestTC37CompileFromConfig:
    """compile_from_config 读取 DB 配置并委托 compile_from_legacy。"""

    def _init_db(self):
        """初始化测试数据库。"""
        from app.services import db_service
        from app.config import load_config

        cfg = load_config()
        db_service.init_db(cfg)
        return cfg

    def test_returns_workflow_graph(self):
        cfg = self._init_db()
        from app.services import db_service

        with db_service.session() as db:
            graph = compile_from_config(db, cfg)
        assert isinstance(graph, WorkflowGraph)

    def test_frozen(self):
        cfg = self._init_db()
        from app.services import db_service

        with db_service.session() as db:
            graph = compile_from_config(db, cfg)
        assert graph.frozen is True

    def test_valid(self):
        cfg = self._init_db()
        from app.services import db_service

        with db_service.session() as db:
            graph = compile_from_config(db, cfg)
        result = graph.validate()
        assert result["ok"], f"校验失败: {result['errors']}"
