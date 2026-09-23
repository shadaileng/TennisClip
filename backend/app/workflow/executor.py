"""工作流执行器：Context 传值 · stage 上报 · 产物投影 · 失败降级。

按拓扑序确定性执行图中各节点，通过 Context 在节点间传递数据。

独立性加固（文档 12 · Step 0）：
- 批次 A1：每节点独立 config 深拷贝（节点内改写 config 不影响其他节点/全局，并行执行前置）
- 批次 A2：节点内传值一律 model_copy（函数式，禁止原地改上游输出）
- 批次 A3：运行时端口值类型校验（spec.validate_port_value，store/resolve 双向）
- 批次 B1：on_failure="skip" 级联跳过（节点失败 → 本节点与下游 skipped，任务仍 SUCCEEDED）
- 批次 B2：产物投影仅信任 output.artifact 显式连线，无兜底扫描
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from app.config import AppConfig
from app.models import TaskResult, TaskStatus, WorkflowNodeProgress
from app.services import db_service
from app.utils.logger import get_logger
from app.workflow.graph import WorkflowGraph
from app.workflow.spec import get_fn, get_spec, resolve_params, validate_port_value

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

    def store(self, node_id: str, outputs: dict[str, Any], port_types: dict[str, str] | None = None) -> None:
        """存储节点输出，并按端口类型做运行时校验（批次 A3）。

        port_types: 端口名 → 类型字符串（来自 NodeSpec.outputs）。
        值类型错误抛 TypeError，定位到本节点输出端口。
        """
        for port, value in (outputs or {}).items():
            ptype = (port_types or {}).get(port)
            if ptype is not None:
                validate_port_value(ptype, value, where=f"store/{node_id}.{port}")
        self._outputs[node_id] = outputs

    def resolve_inputs(self, graph: WorkflowGraph, node_id: str) -> dict[str, Any]:
        """按连线解析节点输入：从上游节点输出取值 + 运行时类型校验（批次 A3）。"""
        inputs: dict[str, Any] = {}
        for edge in graph.edges:
            if edge.to_node_id == node_id:
                src_outputs = self._outputs.get(edge.from_node_id, {})
                value = src_outputs.get(edge.from_port)
                ptype = graph._port_type(edge.from_node_id, edge.from_port)
                if ptype is not None:
                    validate_port_value(ptype, value, where=f"resolve/{edge.from_node_id}.{edge.from_port}")
                inputs[edge.to_port] = value
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
            dead_nodes: set[str] = set()   # 失败/跳过的上游节点（批次 B1 级联依据）
            skipped: list[str] = []

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

                # 解析输入（按连线取值 + 运行时类型校验）
                ctx.inputs = ctx.resolve_inputs(self.graph, node_id)

                # 级联跳过（批次 B1）：必填输入来自死亡/未产出节点 → 本节点 skipped。
                # 判定：该输入值缺失（None 且未注册输出）且唯一来源在 dead_nodes 中。
                # 来源正常执行但输出 None（如 report 节点 all 档位）不级联——由节点自身语义处理。
                should_skip = False
                skip_reason = ""
                for port, value in ctx.inputs.items():
                    if value is not None:
                        continue
                    in_edges = [e for e in self.graph.edges
                                if e.to_node_id == node_id and e.to_port == port]
                    if not in_edges:
                        continue  # 无来源（如 input.video），交给节点自身检查
                    dead_sources = [e.from_node_id for e in in_edges
                                    if e.from_node_id in dead_nodes]
                    if len(dead_sources) == len(in_edges):
                        should_skip = True
                        skip_reason = f"上游 {', '.join(sorted(dead_sources))} 未产出 {port}"
                        break
                if should_skip:
                    idx = node_index_map[node_id]
                    self.result.workflow_nodes[idx].status = "skipped"
                    skipped.append(node_id)
                    dead_nodes.add(node_id)
                    logger.info("workflow: node {} skipped（{}）", node_id, skip_reason)
                    continue

                # 批次 A1：节点级 config 深拷贝隔离（节点内改写不影响其他节点/全局）
                ctx.config = copy.deepcopy(self.config)

                # 更新当前节点为 running
                idx = node_index_map[node_id]
                self.result.workflow_nodes[idx].status = "running"
                self.result.stage = spec.stage  # 保持兼容

                # 缺省值填充 + 校验
                params = resolve_params(spec.params, node.params)

                logger.info("workflow: node={} type={}", node_id, node.type)
                try:
                    outputs = fn(ctx, params)
                    ctx.store(node_id, outputs,
                               port_types={p.name: p.type for p in spec.outputs})
                    self.result.workflow_nodes[idx].status = "done"
                except Exception as exc:
                    if spec.on_failure == "skip" or spec.optional:
                        self.result.workflow_nodes[idx].status = "skipped"
                        skipped.append(node_id)
                        dead_nodes.add(node_id)
                        logger.warning("workflow: 节点 {} 失败（on_failure={}），级联跳过：{}",
                                       node_id, spec.on_failure, exc)
                        continue
                    self.result.workflow_nodes[idx].status = "failed"
                    raise RuntimeError(
                        f"节点 {node_id}({node.type}) 失败：{exc}"
                    ) from exc

            # 批次 B2：产物投影仅信任 output.artifact 显式连线（无兜底扫描）
            if skipped:
                logger.info("workflow: {} 节点 skipped：{}", len(skipped), ", ".join(skipped))
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
