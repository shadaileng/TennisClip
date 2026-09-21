"""工作流执行器：Context 传值 · stage 上报 · 产物投影。

按拓扑序确定性执行图中各节点，通过 Context 在节点间传递数据。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from app.config import AppConfig
from app.models import TaskResult, TaskStatus, WorkflowNodeProgress
from app.services import db_service
from app.utils.logger import get_logger
from app.workflow.graph import WorkflowGraph
from app.workflow.spec import get_fn, get_spec, resolve_params

logger = get_logger(__name__)


@dataclass
class Context:
    """执行上下文：节点间传值、任务元信息。"""
    video_path: Path = field(default_factory=lambda: Path("/tmp/dummy.mp4"))
    config: AppConfig = field(default_factory=lambda: AppConfig())
    task_out: Path = field(default_factory=lambda: Path("/tmp/out"))
    task_id: str = ""
    level: str = "intermediate"
    result: Any = None  # TaskResult 实例
    inputs: dict[str, Any] = field(default_factory=dict)
    # 节点输出存储：outputs[node_id][port_name] = value
    _outputs: dict[str, dict[str, Any]] = field(default_factory=dict)

    def store(self, node_id: str, outputs: dict[str, Any]) -> None:
        """存储节点输出。"""
        self._outputs[node_id] = outputs

    def resolve_inputs(self, graph: WorkflowGraph, node_id: str) -> dict[str, Any]:
        """按连线解析节点输入：从上游节点输出取值。"""
        inputs: dict[str, Any] = {}
        for edge in graph.edges:
            if edge.to_node_id == node_id:
                src_outputs = self._outputs.get(edge.from_node_id, {})
                inputs[edge.to_port] = src_outputs.get(edge.from_port)
        return inputs


class Executor:
    """工作流执行器：按拓扑序执行图中各节点。"""

    def __init__(self, graph: WorkflowGraph, config: AppConfig, result: TaskResult, level: str = "intermediate"):
        self.graph = graph
        self.config = config
        self.result = result
        self.level = level

    def execute(self, video_path: Path) -> TaskResult:
        """执行工作流图，返回结果。"""
        started = time.monotonic()
        task_id = self.result.task_id

        # 每任务独立输出目录
        task_out = self.config.ensure_output_dir() / task_id
        task_out.mkdir(parents=True, exist_ok=True)

        ctx = Context(
            video_path=video_path,
            config=self.config,
            task_out=task_out,
            task_id=task_id,
            level=self.level,
            result=self.result,
        )

        # 记录任务开始
        db_service.record_task_start(task_id, str(video_path), ctx.level)

        try:
            logger.info("workflow: start {} (graph={})", video_path.name, self.graph.name)
            order = self.graph.topo_order()

            # 1) 初始化节点进度列表（按拓扑序，包含所有节点）
            self.result.workflow_nodes = []
            node_index_map: dict[str, int] = {}
            for i, node_id in enumerate(order):
                node = self.graph.nodes[node_id]
                spec = get_spec(node.type)
                self.result.workflow_nodes.append(WorkflowNodeProgress(
                    node_id=node_id,
                    node_type=node.type,
                    label=spec.label if spec else node.type,
                    stage=spec.stage if spec else "unknown",
                    status="pending",
                ))
                node_index_map[node_id] = i

            # 2) 执行时逐节点更新状态
            for node_id in order:
                node = self.graph.nodes[node_id]
                if not node.enabled:
                    continue

                spec = get_spec(node.type)
                if spec is None:
                    raise RuntimeError(f"未知节点类型：{node.type}")

                fn = get_fn(node.type)
                if fn is None:
                    raise RuntimeError(f"节点 {node_id}({node.type}) 无执行函数")

                # 更新当前节点为 running
                idx = node_index_map[node_id]
                self.result.workflow_nodes[idx].status = "running"
                self.result.stage = spec.stage  # 保持兼容

                # 解析输入（按连线取值）
                ctx.inputs = ctx.resolve_inputs(self.graph, node_id)

                # 缺省值填充 + 校验
                params = resolve_params(spec.params, node.params)

                logger.info("workflow: node={} type={}", node_id, node.type)
                try:
                    outputs = fn(ctx, params)
                    ctx.store(node_id, outputs)
                    self.result.workflow_nodes[idx].status = "done"
                except Exception as exc:
                    self.result.workflow_nodes[idx].status = "failed"
                    if spec.optional:
                        logger.warning("workflow: 可选节点 {} 失败，继续：{}", node_id, exc)
                        continue
                    raise RuntimeError(
                        f"节点 {node_id}({node.type}) 失败：{exc}"
                    ) from exc

            # 产物投影：output.artifact 节点已通过 fn 直接写入 result
            # 兜底：若 result.highlight 仍为空，从上下文输出中查找（用户图可能漏连 highlight→output.artifact 边）
            if self.result.highlight is None:
                for _nid, _outs in ctx._outputs.items():
                    hl = _outs.get("highlight")
                    if hl is not None:
                        self.result.highlight = hl
                        logger.info("workflow: highlight 兜底投影 from node={}", _nid)
                        break
            self.result.status = TaskStatus.SUCCEEDED
            self.result.elapsed_seconds = time.monotonic() - started
            logger.info("workflow: done in {:.1f}s", self.result.elapsed_seconds)

        except Exception as exc:
            self.result.status = TaskStatus.FAILED
            self.result.error = str(exc)
            self.result.elapsed_seconds = time.monotonic() - started
            logger.exception("workflow failed: {}", exc)

        # 落库：任务终态 + 结果快照
        db_service.record_task_finish(
            task_id=task_id,
            status=self.result.status,
            elapsed_seconds=self.result.elapsed_seconds,
            error=self.result.error,
            highlight_json=self.result.highlight.model_dump() if self.result.highlight else None,
            report_json=self.result.report.model_dump() if self.result.report else None,
            generated_by=self.result.report.generated_by if self.result.report else "",
        )

        return self.result
