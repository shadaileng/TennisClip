"""工作流图结构：节点 + 边 + 校验 + 拓扑排序。

WorkflowGraph 是工作流的唯一真相源：定义了节点类型、连接关系与参数。
校验规则（R1~R10）确保图的合法性；Kahn 拓扑排序提供确定性执行顺序。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

from app.utils.logger import get_logger
from app.workflow.spec import get_spec, resolve_params

logger = get_logger(__name__)


@dataclass
class GraphNode:
    """图中节点实例。"""
    id: str
    type: str
    params: dict = field(default_factory=dict)
    enabled: bool = True


@dataclass
class GraphEdge:
    """图中边实例：from_node.port → to_node.port。"""
    id: str
    from_node_id: str
    from_port: str
    to_node_id: str
    to_port: str


class WorkflowGraph:
    """可编排工作流图：节点 + 有向边 + 校验 + 拓扑排序。"""

    def __init__(self, name: str = "", nodes: list[GraphNode] = None, edges: list[GraphEdge] = None):
        self.name = name
        self._nodes_list: list[GraphNode] = nodes or []
        self.edges: list[GraphEdge] = edges or []
        # 快速查找（允许重复 id 时取第一个，校验时用 _nodes_list）
        self.nodes: dict[str, GraphNode] = {}
        for n in self._nodes_list:
            if n.id not in self.nodes:
                self.nodes[n.id] = n
        self.frozen: bool = False  # 预置图编译后锁定，禁止修改

    def add_node(self, node_id: str, node_type: str, params: dict = None, default_output: str | None = None) -> None:
        """添加节点（frozen 时抛 RuntimeError）。"""
        if self.frozen:
            raise RuntimeError("工作流已锁定，无法修改节点")
        node = GraphNode(id=node_id, type=node_type, params=params or {})
        self._nodes_list.append(node)
        if node_id not in self.nodes:
            self.nodes[node_id] = node

    def add_edge(self, from_id: str, from_port: str, to_id: str, to_port: str) -> None:
        """添加边（frozen 时抛 RuntimeError）。"""
        if self.frozen:
            raise RuntimeError("工作流已锁定，无法修改边")
        edge_id = f"{from_id}.{from_port}-{to_id}.{to_port}"
        self.edges.append(GraphEdge(
            id=edge_id,
            from_node_id=from_id,
            from_port=from_port,
            to_node_id=to_id,
            to_port=to_port,
        ))

    @classmethod
    def from_dict(cls, data: dict) -> WorkflowGraph:
        """从 dict（JSON 反序列化）构建 WorkflowGraph。"""
        nodes = []
        for nd in data.get("nodes", []):
            nodes.append(GraphNode(
                id=nd["id"],
                type=nd["type"],
                params=nd.get("params", {}),
                enabled=nd.get("enabled", True),
            ))
        edges = []
        for ed in data.get("edges", []):
            from_parts = ed["from"]
            to_parts = ed["to"]
            edges.append(GraphEdge(
                id=ed["id"],
                from_node_id=from_parts[0],
                from_port=from_parts[1],
                to_node_id=to_parts[0],
                to_port=to_parts[1],
            ))
        return cls(name=data.get("name", ""), nodes=nodes, edges=edges)

    def to_dict(self) -> dict:
        """序列化为 dict（可 JSON 化）。"""
        return {
            "version": 1,
            "name": self.name,
            "nodes": [
                {"id": n.id, "type": n.type, "params": n.params, "enabled": n.enabled}
                for n in self._nodes_list
            ],
            "edges": [
                {"id": e.id, "from": [e.from_node_id, e.from_port], "to": [e.to_node_id, e.to_port]}
                for e in self.edges
            ],
        }

    def validate(self) -> dict[str, Any]:
        """校验图合法性，返回 {ok: bool, errors: [{node, code, message}]}。

        校验规则 R1~R10：不抛异常，返回结构化结果。
        """
        errors: list[dict] = []
        # 用 lookup dict 快速查找（非重复节点）
        lookup = self.nodes

        # R1: 节点类型已注册
        for n in self._nodes_list:
            if get_spec(n.type) is None:
                errors.append({"node": n.id, "code": "R1", "message": f"未知节点类型：{n.type}"})

        # R2: 节点 id 唯一
        seen_ids: set[str] = set()
        for n in self._nodes_list:
            if n.id in seen_ids:
                errors.append({"node": n.id, "code": "R2", "message": f"节点 id 重复：{n.id}"})
            seen_ids.add(n.id)

        # R3: 端口存在
        for e in self.edges:
            src_node = lookup.get(e.from_node_id)
            if src_node:
                src_spec = get_spec(src_node.type)
                if src_spec:
                    out_names = {p.name for p in src_spec.outputs}
                    if e.from_port not in out_names:
                        errors.append({"node": e.from_node_id, "code": "R3",
                                       "message": f"节点 {e.from_node_id} 不存在输出端口 {e.from_port}"})
            tgt_node = lookup.get(e.to_node_id)
            if tgt_node:
                tgt_spec = get_spec(tgt_node.type)
                if tgt_spec:
                    in_names = {p.name for p in tgt_spec.inputs}
                    if e.to_port not in in_names:
                        errors.append({"node": e.to_node_id, "code": "R3",
                                       "message": f"节点 {e.to_node_id} 不存在输入端口 {e.to_port}"})

        # R4: 端口类型匹配
        for e in self.edges:
            src_type = self._port_type(e.from_node_id, e.from_port)
            tgt_type = self._port_type(e.to_node_id, e.to_port)
            if src_type and tgt_type and src_type != tgt_type:
                errors.append({"node": e.from_node_id, "code": "R4",
                               "message": f"连线类型不匹配：{src_type} → {tgt_type}（{e.from_node_id}.{e.from_port} → {e.to_node_id}.{e.to_port}）"})

        # R5: 必填输入已连线
        enabled_set = {n.id for n in self._nodes_list if n.enabled}
        for nid in enabled_set:
            node = lookup[nid]
            spec = get_spec(node.type)
            if spec is None:
                continue
            in_edges = [e for e in self.edges if e.to_node_id == nid]
            connected_ports = {e.to_port for e in in_edges}
            for port in spec.inputs:
                if port.required and port.name not in connected_ports:
                    errors.append({"node": nid, "code": "R5",
                                   "message": f"节点 {nid} 的必填输入 {port.name} 未连接"})

        # R6: 单输入端口唯一
        for nid in enabled_set:
            in_edges = [e for e in self.edges if e.to_node_id == nid]
            port_count: dict[str, int] = {}
            for e in in_edges:
                port_count[e.to_port] = port_count.get(e.to_port, 0) + 1
            for port_name, count in port_count.items():
                if count > 1:
                    errors.append({"node": nid, "code": "R6",
                                   "message": f"节点 {nid} 的输入端口 {port_name} 存在多条连线"})

        # R7: 无环（Kahn 拓扑排序）
        enabled_nodes = {nid: lookup[nid] for nid in enabled_set}
        if enabled_nodes:
            order = self._kahn_order(enabled_nodes)
            if order is None:
                errors.append({"node": "", "code": "R7", "message": "工作流存在环"})

        # R8: 存在输出节点
        has_output = any(
            n.type == "output.artifact" and n.enabled
            for n in self._nodes_list
        )
        if not has_output:
            errors.append({"node": "", "code": "R8", "message": "工作流缺少 output.artifact 输出节点"})

        # R9: 孤立节点
        for nid in enabled_set:
            in_edges = [e for e in self.edges if e.to_node_id == nid]
            out_edges = [e for e in self.edges if e.from_node_id == nid]
            if not in_edges and not out_edges:
                errors.append({"node": nid, "code": "R9", "message": f"孤立节点：{nid}"})

        # R10: 参数合法
        for nid in enabled_set:
            node = lookup[nid]
            spec = get_spec(node.type)
            if spec is None:
                continue
            try:
                resolve_params(spec.params, node.params)
            except ValueError as exc:
                errors.append({"node": nid, "code": "R10", "message": str(exc)})

        # R8 补充（批次 B2）：output.artifact 的 highlight 入边检查。
        # 有 highlight 生产节点参与时缺连 → 阻断（ok=False）；
        # 图中无 highlight 生产节点时缺连 → 仅 warning（不阻断，用户可能有意只要视频/报告）。
        hl_producers = {
            n.id for n in self._nodes_list
            if get_spec(n.type) and any(p.name == "highlight" for p in get_spec(n.type).outputs)
        }
        for n in self._nodes_list:
            if n.type == "output.artifact" and n.enabled:
                hl_in_edges = [
                    e for e in self.edges
                    if e.to_node_id == n.id and e.to_port == "highlight"
                ]
                if not hl_in_edges:
                    if hl_producers:
                        errors.append({"node": n.id, "code": "R8",
                                        "message": f"节点 {n.id}（output.artifact）缺少 highlight 入边，集锦将不出现在结果中"})
                    else:
                        logger.warning("workflow: 节点 {}（output.artifact）缺少 highlight 入边（无 highlight 生产节点，仅提示）", n.id)

        return {"ok": len(errors) == 0, "errors": errors}

    def topo_order(self) -> list[str]:
        """Kahn 拓扑排序：确定性顺序，同层按 node.id 升序出队。"""
        enabled = {nid: self.nodes[nid] for nid in self.nodes if self.nodes[nid].enabled}
        order = self._kahn_order(enabled)
        if order is None:
            raise ValueError("工作流存在环，无法拓扑排序")
        return order

    def _kahn_order(self, enabled_nodes: dict[str, GraphNode]) -> Optional[list[str]]:
        """Kahn 算法：返回拓扑序（同层 id 升序），存在环返回 None。"""
        in_degree: dict[str, int] = {nid: 0 for nid in enabled_nodes}
        adj: dict[str, list[str]] = {nid: [] for nid in enabled_nodes}
        for e in self.edges:
            if e.from_node_id in enabled_nodes and e.to_node_id in enabled_nodes:
                adj[e.from_node_id].append(e.to_node_id)
                in_degree[e.to_node_id] = in_degree.get(e.to_node_id, 0) + 1

        # 入度为 0 的节点按 id 升序入队
        queue = deque(sorted([nid for nid, deg in in_degree.items() if deg == 0]))
        order: list[str] = []

        while queue:
            nid = queue.popleft()
            order.append(nid)
            for next_id in sorted(adj[nid]):
                in_degree[next_id] -= 1
                if in_degree[next_id] == 0:
                    queue.append(next_id)
                    queue = deque(sorted(queue))

        if len(order) != len(enabled_nodes):
            return None  # 有环
        return order

    def _port_type(self, node_id: str, port_name: str) -> Optional[str]:
        """获取节点指定端口的类型。"""
        node = self.nodes.get(node_id)
        if node is None:
            return None
        spec = get_spec(node.type)
        if spec is None:
            return None
        for p in spec.inputs + spec.outputs:
            if p.name == port_name:
                return p.type
        return None
