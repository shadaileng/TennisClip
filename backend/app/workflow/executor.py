"""工作流执行器：Context 传值 · stage 上报 · 产物投影 · 失败降级 · 分层并行。

按 Kahn 分层（topo_levels）确定性执行图中各节点，通过 Context 在节点间传递数据。

独立性加固（文档 12 · Step 0）：
- 批次 A1：每节点独立 config 深拷贝（节点内改写 config 不影响其他节点/全局，并行执行前置）
- 批次 A2：节点内传值一律 model_copy（函数式，禁止原地改上游输出）
- 批次 A3：运行时端口值类型校验（spec.validate_port_value，store/resolve 双向）
- 批次 B1：on_failure="skip" 级联跳过（节点失败 → 本节点与下游 skipped，任务仍 SUCCEEDED）；
  optional=True 节点失败仅标 skipped、不记死亡（下游按无产出继续，保留既有产物投影）
- 批次 B2：产物投影仅信任 output.artifact 显式连线，无兜底扫描
- 批次 C1（同层并行）：准备（解析输入/级联判定/参数）与收尾（store/落库/状态）在主线程
  按层内 node.id 升序执行（日志与状态确定可复现）；同层节点按 spec.stage 分组——组间
  串行（result.stage 标量字段的阶段上报顺序确定），组内 ≥2 节点线程池并发
  （单节点组内联调用，链式图保持串行语义与零线程开销）
- 批次 C2（落库解耦）：节点不直调 db_service——输入元信息按 NodeSpec.records_input、
  产物文件按 NodeSpec.persists 由执行器在收尾阶段统一落库；全部收尾写在主线程，
  并行执行期不存在并发 DB 写（规避 SQLite 多线程写锁风险）
"""

from __future__ import annotations

import copy
import dataclasses
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from app.config import AppConfig
from app.models import TaskResult, TaskStatus, WorkflowNodeProgress
from app.services import db_service
from app.utils import cancel
from app.utils.logger import get_logger
from app.workflow.graph import WorkflowGraph
from app.workflow.spec import get_fn, get_spec, resolve_params, validate_port_value

logger = get_logger(__name__)

# 批次 C1：同层并行的最大线程数（单机工具，够用即可；层内节点数更少时取其值）
_MAX_LEVEL_WORKERS = 8


def _run_node(fn, node_ctx: "Context", params: dict[str, Any]) -> tuple[str, Any]:
    """线程池包装：节点调用捕获异常为 ("ok", outputs) | ("err", exc)，本身永不抛出。"""
    try:
        return ("ok", fn(node_ctx, params))
    except Exception as exc:  # noqa: BLE001
        return ("err", exc)


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
    """工作流执行器：按 Kahn 分层执行图中各节点（同阶段同层节点并行）。"""

    def __init__(self, graph: WorkflowGraph, config: AppConfig, result: TaskResult, level: str = "intermediate"):
        self.graph = graph
        self.config = config
        self.result = result
        self.level = level

    def _record_input(self, spec, outputs: dict[str, Any], ctx: Context) -> None:
        """声明式输入元信息落库（批次 C2：NodeSpec.records_input）。

        video_path/level 取 Context，duration_seconds 取 "duration" 输出端口
        （缺省回退 "meta" 端口），width/height/fps 取 "meta" 输出端口 dict。
        """
        if not spec.records_input:
            return
        outputs = outputs or {}
        meta = outputs.get("meta") or {}
        duration = outputs.get("duration")
        if duration is None:
            duration = meta.get("duration")
        db_service.record_task_input(
            task_id=ctx.task_id,
            video_path=Path(ctx.video_path),
            duration_seconds=duration,
            width=meta.get("width"),
            height=meta.get("height"),
            fps=meta.get("fps"),
            level=ctx.level,
        )

    def _record_outputs(self, spec, outputs: dict[str, Any], ctx: Context) -> None:
        """声明式产物落库（批次 C2：NodeSpec.persists）。

        对每个 (输出端口, outputs.kind) 对：路径类端口值直接作为产物文件；
        非路径值（如 TechnicalReport）按约定查 task_out/{kind}.json。
        文件不存在跳过（等价原节点的 exists 守卫）。kind == "highlight_video"
        时附带 target_duration（集锦时长展示）。仅在主线程收尾阶段调用。
        """
        for port, kind in (spec.persists or ()):
            value = (outputs or {}).get(port)
            if value is None:
                continue
            if isinstance(value, (str, Path)):
                file_path = Path(value)
            else:
                file_path = Path(ctx.task_out) / f"{kind}.json"
            if not file_path.is_file():
                continue
            size_mb = file_path.stat().st_size / (1024 * 1024)
            db_service.record_task_output(
                ctx.task_id, kind, str(file_path), size_mb,
                target_duration=(
                    self.config.highlight.target_duration
                    if kind == "highlight_video" else None
                ),
            )

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
            levels = self.graph.topo_levels()

            # 1) 初始化节点进度列表（按分层展平 = 合法拓扑序，包含所有启用节点）
            order = [nid for level in levels for nid in level]
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

            # 2) 分层执行（批次 C1）：准备/收尾主线程确定序，同层多节点线程池并行
            dead_nodes: set[str] = set()   # 失败/跳过的上游节点（批次 B1 级联依据）
            skipped: list[str] = []

            for level in levels:
                # ---- 节点边界取消检查点（批次 C1）----
                # 用户停止任务：在进入下一层前检测旗标，命中即抛 TaskCancelled，
                # 走下方失败分支（record_task_finish 落库 failed，重启不会复活）。
                cancel.check_cancelled(task_id, "workflow 节点边界")

                # ---- 准备阶段（主线程串行，层内 id 升序）----
                runnable: list[tuple] = []  # (node_id, spec, fn, params, node_ctx, idx)
                for node_id in level:
                    node = self.graph.nodes[node_id]
                    if not node.enabled:
                        continue

                    spec = get_spec(node.type)
                    if spec is None:
                        raise RuntimeError(f"未知节点类型：{node.type}")

                    fn = get_fn(node.type)
                    if fn is None:
                        raise RuntimeError(f"节点 {node_id}({node.type}) 无执行函数")

                    # 解析输入（按连线取值 + 运行时类型校验；输入只来自已完成的上游层）
                    inputs = ctx.resolve_inputs(self.graph, node_id)

                    # 级联跳过（批次 B1）：必填输入来自死亡/未产出节点 → 本节点 skipped。
                    # 判定：该输入值缺失（None 且未注册输出）且唯一来源在 dead_nodes 中。
                    # 来源正常执行但输出 None（如 report 节点 all 档位）不级联——由节点自身语义处理。
                    # 死亡锚点见收尾阶段：on_failure=skip 与级联跳过的节点记入 dead_nodes；
                    # optional=True 节点失败仅标 skipped 不记死亡（下游按无产出继续，保留既有产物）。
                    should_skip = False
                    skip_reason = ""
                    for port, value in inputs.items():
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

                    # 缺省值填充 + 校验
                    params = resolve_params(spec.params, node.params)

                    # 更新当前节点为 running（stage 于执行分组时上报，见下方 C1）
                    idx = node_index_map[node_id]
                    self.result.workflow_nodes[idx].status = "running"

                    logger.info("workflow: node={} type={}", node_id, node.type)

                    # 节点级上下文：独立 inputs + 批次 A1 config 深拷贝隔离（并行执行的节点互不可见）
                    node_ctx = dataclasses.replace(
                        ctx,
                        inputs=inputs,
                        config=copy.deepcopy(self.config),
                    )
                    runnable.append((node_id, spec, fn, params, node_ctx, idx))

                if not runnable:
                    continue

                # ---- 执行阶段（批次 C1）：按 stage 分组串行上报，组内同阶段节点并行 ----
                # result.stage 是标量，同层跨阶段节点并发上报无法确定性观测（fns/UI 读到
                # 哪个阶段取决于线程时序）——按 spec.stage 分组：组间串行（阶段顺序确定），
                # 组内 ≥2 节点线程池并行（典型场景：detect.* 同为 detecting、analyze 双分支
                # 同为 highlighting）；单节点组内联调用（链式图零线程开销、串行语义一致）。
                outcomes: dict[str, tuple[str, Any]] = {}
                stage_groups: dict[str, list[tuple]] = {}
                for item in runnable:  # runnable 已按层内 node.id 升序 → 组序 = 首现（最小 id）序
                    stage_groups.setdefault(item[1].stage, []).append(item)

                for stage, group in stage_groups.items():
                    self.result.stage = stage  # 阶段上报（组内节点同 stage，读取一致）
                    if len(group) == 1:
                        node_id, spec, fn, params, node_ctx, idx = group[0]
                        outcomes[node_id] = _run_node(fn, node_ctx, params)
                    else:
                        logger.info("workflow: 并行执行同阶段 {} 个节点（stage={}）：{}",
                                    len(group), stage, ", ".join(item[0] for item in group))
                        with ThreadPoolExecutor(
                            max_workers=min(_MAX_LEVEL_WORKERS, len(group)),
                        ) as pool:
                            future_map = {
                                pool.submit(_run_node, fn, node_ctx, params): node_id
                                for (node_id, spec, fn, params, node_ctx, idx) in group
                            }
                            for fut in as_completed(future_map):
                                outcomes[future_map[fut]] = fut.result()

                # ---- 收尾阶段（主线程按层内 id 升序）：store → 声明式落库 → 状态 ----
                # 并行期间所有 DB 写/状态写集中于此，顺序确定（批次 C1/C2）
                fatal: Optional[Exception] = None
                fatal_node = ""
                for node_id, spec, fn, params, node_ctx, idx in runnable:
                    state, payload = outcomes[node_id]
                    try:
                        if state == "err":
                            raise payload  # noqa: B904 —— 统一走下方失败分支处理
                        ctx.store(node_id, payload,
                                  port_types={p.name: p.type for p in spec.outputs})
                        # 批次 C2：声明式落库（records_input / persists），节点保持纯函数
                        self._record_input(spec, payload, node_ctx)
                        self._record_outputs(spec, payload, node_ctx)
                        self.result.workflow_nodes[idx].status = "done"
                    except Exception as exc:
                        if spec.on_failure == "skip":
                            # skip 策略 = 级联锚点：自身与下游标 skipped（AGENTS 契约）
                            self.result.workflow_nodes[idx].status = "skipped"
                            skipped.append(node_id)
                            dead_nodes.add(node_id)
                            logger.warning("workflow: 节点 {} 失败（on_failure={}），级联跳过：{}",
                                           node_id, spec.on_failure, exc)
                            continue
                        if spec.optional:
                            # best-effort 失败：仅标 skipped、不记死亡——下游按「来源存活、
                            # 无产出」继续执行（如 report 失败保留已生成的集锦/高光投影）
                            self.result.workflow_nodes[idx].status = "skipped"
                            skipped.append(node_id)
                            logger.warning("workflow: 节点 {} 失败（optional），跳过且不级联：{}",
                                           node_id, exc)
                            continue
                        self.result.workflow_nodes[idx].status = "failed"
                        if fatal is None:
                            fatal = exc
                            fatal_node = node_id
                if fatal is not None:
                    raise RuntimeError(
                        f"节点 {fatal_node}({self.graph.nodes[fatal_node].type}) 失败：{fatal}"
                    ) from fatal

            # 批次 B2：产物投影仅信任 output.artifact 显式连线（无兜底扫描）
            if skipped:
                logger.info("workflow: {} 节点 skipped：{}", len(skipped), ", ".join(skipped))
            self.result.status = TaskStatus.SUCCEEDED
            self.result.elapsed_seconds = time.monotonic() - started
            logger.info("workflow: done in {:.1f}s", self.result.elapsed_seconds)

        except cancel.TaskCancelled as exc:
            # 用户显式停止：走 failed 分支但给出明确原因，避免误读为系统故障
            self.result.status = TaskStatus.FAILED
            self.result.error = f"任务已被用户停止（{task_id}）"
            self.result.elapsed_seconds = time.monotonic() - started
            logger.info("workflow: 任务 {} 被用户停止", task_id)
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
        # 任务已终态：清除取消旗标（避免内存泄漏；已 failed 不会复活）
        cancel.clear_cancelled(task_id)

        return self.result
