"""预置工作流编译器：从旧配置 KV 构建默认 WorkflowGraph。

- compile_from_legacy(stages, strategy, level) → WorkflowGraph
- compile_from_config(db, config) → WorkflowGraph（读 KV 后委托 compile_from_legacy）
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.utils.logger import get_logger

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.config import AppConfig

logger = get_logger(__name__)


def compile_from_legacy(
    stages: list[str] | None = None,
    strategy: str = "frame",
    level: str = "intermediate",
) -> "WorkflowGraph":
    """从旧管线配置三元组编译默认工作流图。

    每次调用返回全新图实例（可直接丢弃旧图）。
    """
    from app.workflow.graph import GraphEdge, GraphNode, WorkflowGraph

    if stages is None:
        stages = ["preprocess", "highlight", "edit", "report"]

    enabled_set = set(stages)

    # 标准节点 ID
    _N_INPUT = "n1"
    _N_PREPROCESS = "n2"
    _N_DETECT = "n3"
    _N_ANALYZE = "n4"
    _N_FILTER = "n5"
    _N_UNIFORM = "n6"
    _N_EDIT = "n7"
    _N_REPORT = "n8"
    _N_OUTPUT = "n9"

    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    edge_counter = [0]

    def _next_edge_id() -> str:
        edge_counter[0] += 1
        return f"e{edge_counter[0]}"

    def _add_edge(from_id: str, from_port: str, to_id: str, to_port: str):
        edges.append(GraphEdge(
            id=_next_edge_id(),
            from_node_id=from_id, from_port=from_port,
            to_node_id=to_id, to_port=to_port,
        ))

    # --- Input ---
    nodes.append(GraphNode(id=_N_INPUT, type="input.video"))

    # --- Preprocess ---
    if "preprocess" in enabled_set:
        nodes.append(GraphNode(id=_N_PREPROCESS, type="preprocess.transcode"))
        _add_edge(_N_INPUT, "video", _N_PREPROCESS, "video")

    # Video source for downstream nodes
    video_ref = _N_PREPROCESS if "preprocess" in enabled_set else _N_INPUT

    # --- Highlight stage ---
    if "highlight" in enabled_set:
        # Detect candidates
        nodes.append(GraphNode(id=_N_DETECT, type="detect.candidates"))
        _add_edge(video_ref, "video", _N_DETECT, "video")
        if "preprocess" in enabled_set:
            _add_edge(_N_PREPROCESS, "duration", _N_DETECT, "duration")

        # Analyze highlight
        nodes.append(GraphNode(
            id=_N_ANALYZE, type="analyze.highlight",
            params={"level": level, "analysis_mode": strategy},
        ))
        _add_edge(_N_DETECT, "candidates", _N_ANALYZE, "candidates")
        _add_edge(video_ref, "video", _N_ANALYZE, "video")
        if "preprocess" in enabled_set:
            _add_edge(_N_PREPROCESS, "duration", _N_ANALYZE, "duration")

        # Post filter (level != 'all')
        if level != "all":
            nodes.append(GraphNode(id=_N_FILTER, type="post.filter_segments"))
            _add_edge(_N_ANALYZE, "highlight", _N_FILTER, "highlight")

        # Post uniform — takes duration only (generates its own highlight as fallback)
        nodes.append(GraphNode(id=_N_UNIFORM, type="post.uniform_slices"))
        if "preprocess" in enabled_set:
            _add_edge(_N_PREPROCESS, "duration", _N_UNIFORM, "duration")

    # --- Edit ---
    if "edit" in enabled_set:
        nodes.append(GraphNode(id=_N_EDIT, type="edit.concat"))
        # highlight source: post_filter if exists, else analyze directly
        highlight_src = _N_FILTER if ("highlight" in enabled_set and level != "all") else (
            _N_ANALYZE if "highlight" in enabled_set else video_ref
        )
        if "highlight" in enabled_set:
            _add_edge(highlight_src, "highlight", _N_EDIT, "highlight")
        else:
            _add_edge(video_ref, "video", _N_EDIT, "video")
        _add_edge(video_ref, "video", _N_EDIT, "video")

    # --- Report (level='all' 时不生成技术分析报告) ---
    if "report" in enabled_set and level != "all":
        nodes.append(GraphNode(
            id=_N_REPORT, type="report.technical",
            params={"level": level},
        ))
        highlight_src = _N_FILTER if ("highlight" in enabled_set and level != "all") else (
            _N_ANALYZE if "highlight" in enabled_set else video_ref
        )
        if "highlight" in enabled_set:
            _add_edge(highlight_src, "highlight", _N_REPORT, "highlight")
        else:
            _add_edge(video_ref, "video", _N_REPORT, "video")
        _add_edge(video_ref, "video", _N_REPORT, "video")

    # --- Output ---
    nodes.append(GraphNode(id=_N_OUTPUT, type="output.artifact"))
    if "edit" in enabled_set:
        _add_edge(_N_EDIT, "video", _N_OUTPUT, "video")
    if "report" in enabled_set and level != "all":
        _add_edge(_N_REPORT, "report", _N_OUTPUT, "report")
    # 高光结果显式投影（批次 B2：执行器不再做兜底扫描，预置图必须连出 highlight→output.artifact）
    if "highlight" in enabled_set:
        _add_edge(_N_UNIFORM, "highlight", _N_OUTPUT, "highlight")

    graph = WorkflowGraph(name="default", nodes=nodes, edges=edges)
    graph.frozen = True

    logger.info(
        "compile_from_legacy: stages={} strategy={} level={} nodes={} edges={}",
        stages, strategy, level, len(graph.nodes), len(graph.edges),
    )

    return graph


def compile_from_config(db: "Session", config: "AppConfig") -> "WorkflowGraph":
    """从配置 KV 解析并编译默认工作流图。"""
    from app.services.config_service import get_pipeline_config

    cfg = get_pipeline_config(db, config)
    return compile_from_legacy(
        stages=cfg["enabled_stages"],
        strategy=cfg.get("highlight_strategy", "frame"),
        level=cfg.get("level", "intermediate"),
    )
