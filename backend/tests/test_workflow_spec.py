"""Step 1 测试：节点契约与注册表。

TC-01 ~ TC-05：端口类型常量、NodeSpec 注册/查询/不可变、参数缺省与越界、Schema 序列化。
"""

from __future__ import annotations

import json

import pytest

from app.workflow.spec import (
    NodeSpec,
    ParamSpec,
    Port,
    PortType,
    build_schema,
    get_spec,
    list_specs,
    register,
    resolve_params,
)


# ---------- TC-01: register 后可按 type 取到 NodeSpec ----------

def test_register_and_get_spec():
    """register 后可按 type 取到 NodeSpec，inputs/outputs/params 完整且不可变。"""

    @register(
        NodeSpec(
            type="test.register_node",
            label="注册测试节点",
            category="test",
            description="注入任务源视频",
            inputs=[],
            outputs=[Port(name="video", type=PortType.VIDEO)],
            params=[],
            stage="input",
        )
    )
    def _input_video(ctx, params):
        return {}

    spec = get_spec("test.register_node")
    assert spec is not None
    assert spec.type == "test.register_node"
    assert spec.label == "注册测试节点"
    assert spec.category == "test"
    assert len(spec.outputs) == 1
    assert spec.outputs[0].name == "video"
    assert spec.outputs[0].type == PortType.VIDEO
    assert len(spec.inputs) == 0
    assert len(spec.params) == 0

    # 不可变性：NodeSpec 是 frozen dataclass
    with pytest.raises(AttributeError):
        spec.type = "other.type"  # type: ignore[misc]


# ---------- TC-02: build_schema() 结果可 JSON 序列化 ----------

def test_build_schema_json_serializable():
    """build_schema() 结果含全部已注册节点与其参数默认值，可 JSON 序列化。"""
    # 先注册一个带参数的节点
    @register(
        NodeSpec(
            type="test.schema_node",
            label="Schema 测试节点",
            category="test",
            description="",
            inputs=[Port(name="video", type=PortType.VIDEO)],
            outputs=[Port(name="highlight", type=PortType.HIGHLIGHT)],
            params=[
                ParamSpec(key="level", label="层级", type="select", default="intermediate",
                          options=["beginner", "intermediate", "professional"]),
                ParamSpec(key="max_segments", label="最大段数", type="int", default=3,
                          min=1, max=10),
            ],
            stage="highlight",
        )
    )
    def _schema_node(ctx, params):
        return {}

    schema = build_schema()
    assert isinstance(schema, list)
    assert len(schema) >= 2  # input.video + test.schema_node

    # 找到我们注册的节点
    node_schemas = [n for n in schema if n["type"] == "test.schema_node"]
    assert len(node_schemas) == 1
    node = node_schemas[0]
    assert node["label"] == "Schema 测试节点"
    assert len(node["params"]) == 2

    # 参数默认值
    params = {p["key"]: p for p in node["params"]}
    assert params["level"]["default"] == "intermediate"
    assert params["level"]["options"] == ["beginner", "intermediate", "professional"]
    assert params["max_segments"]["default"] == 3
    assert params["max_segments"]["min"] == 1
    assert params["max_segments"]["max"] == 10

    # JSON 序列化不报错
    json_str = json.dumps(schema, ensure_ascii=False)
    assert "test.schema_node" in json_str

    # 批次 B1：schema 携带 on_failure 字段（默认 fail）
    assert "on_failure" in node
    assert node["on_failure"] == "fail"


# ---------- TC-03: 重复注册同一 type 抛 ValueError ----------

def test_duplicate_register_raises():
    """重复注册同一 type 应抛 ValueError（含重复 type 文案）。"""
    @register(
        NodeSpec(
            type="test.duplicate_node",
            label="Dup",
            category="test",
            description="",
            inputs=[],
            outputs=[],
            params=[],
            stage="test",
        )
    )
    def _dup1(ctx, params):
        return {}

    with pytest.raises(ValueError, match="test.duplicate_node"):
        @register(
            NodeSpec(
                type="test.duplicate_node",
                label="Dup2",
                category="test",
                description="",
                inputs=[],
                outputs=[],
                params=[],
                stage="test",
            )
        )
        def _dup2(ctx, params):
            return {}


# ---------- TC-04: 参数缺省由 ParamSpec.default 填充；越界抛清晰错误 ----------

def test_resolve_params_defaults():
    """参数缺省由 ParamSpec.default 填充。"""
    specs = [
        ParamSpec(key="level", label="层级", type="select", default="intermediate",
                  options=["beginner", "intermediate", "professional"]),
        ParamSpec(key="count", label="数量", type="int", default=3, min=1, max=10),
    ]
    resolved = resolve_params(specs, {})
    assert resolved["level"] == "intermediate"
    assert resolved["count"] == 3


def test_resolve_params_explicit():
    """显式传入的参数应覆盖默认值。"""
    specs = [
        ParamSpec(key="level", label="层级", type="select", default="intermediate",
                  options=["beginner", "intermediate", "professional"]),
    ]
    resolved = resolve_params(specs, {"level": "professional"})
    assert resolved["level"] == "professional"


def test_resolve_params_select_out_of_range():
    """select 取值越界抛清晰错误。"""
    specs = [
        ParamSpec(key="level", label="层级", type="select", default="intermediate",
                  options=["beginner", "intermediate", "professional"]),
    ]
    with pytest.raises(ValueError, match="level"):
        resolve_params(specs, {"level": "expert"})


def test_resolve_params_int_out_of_range():
    """int 越界（低于 min）抛清晰错误。"""
    specs = [
        ParamSpec(key="count", label="数量", type="int", default=3, min=1, max=10),
    ]
    with pytest.raises(ValueError, match="count"):
        resolve_params(specs, {"count": 0})


def test_resolve_params_int_above_max():
    """int 越界（高于 max）抛清晰错误。"""
    specs = [
        ParamSpec(key="count", label="数量", type="int", default=3, min=1, max=10),
    ]
    with pytest.raises(ValueError, match="count"):
        resolve_params(specs, {"count": 11})


def test_resolve_params_float_in_range():
    """float 在范围内应正常通过。"""
    specs = [
        ParamSpec(key="threshold", label="阈值", type="float", default=0.5, min=0.0, max=1.0),
    ]
    resolved = resolve_params(specs, {"threshold": 0.8})
    assert resolved["threshold"] == 0.8


def test_resolve_params_float_out_of_range():
    """float 越界抛清晰错误。"""
    specs = [
        ParamSpec(key="threshold", label="阈值", type="float", default=0.5, min=0.0, max=1.0),
    ]
    with pytest.raises(ValueError, match="threshold"):
        resolve_params(specs, {"threshold": 1.5})


# ---------- 批次 A4: 自动发现注册（新增节点放文件即注册）----------

def test_auto_discovery_registers_new_node(tmp_path, monkeypatch):
    """nodes/ 包内新增模块导入后自动注册，无需改 __init__.py（批次 A4）。"""
    from app.workflow import nodes as nodes_pkg
    from app.workflow.spec import _FN_REGISTRY, _SPEC_REGISTRY

    # 动态创建新节点模块文件（模拟"放文件即注册"）
    module_path = tmp_path / "test_auto_node_tmp.py"
    module_path.write_text(
        'from app.workflow.spec import NodeSpec, Port, PortType, register\n'
        '\n'
        '@register(NodeSpec(\n'
        '    type="test.auto_discovered",\n'
        '    label="自动发现节点",\n'
        '    category="test",\n'
        '    description="",\n'
        '    inputs=[],\n'
        '    outputs=[Port(name="video", type=PortType.VIDEO)],\n'
        '    params=[],\n'
        '    stage="test",\n'
        '))\n'
        'def run(ctx, params):\n'
        '    return {"video": ctx.video_path}\n',
        encoding="utf-8",
    )

    # 将 tmp_path 临时挂到 nodes 包路径上，使 pkgutil 能发现新模块
    import sys
    monkeypatch.setattr(nodes_pkg, "__path__", list(nodes_pkg.__path__) + [str(tmp_path)])
    module_name = f"{nodes_pkg.__name__}.test_auto_node_tmp"
    if module_name in sys.modules:
        del sys.modules[module_name]

    try:
        import importlib
        importlib.import_module(module_name)
        # 新模块导入即注册
        assert "test.auto_discovered" in _SPEC_REGISTRY
        assert "test.auto_discovered" in _FN_REGISTRY
        # 现有节点不受影响
        assert "input.video" in _SPEC_REGISTRY
    finally:
        module_path.unlink(missing_ok=True)
        sys.modules.pop(module_name, None)


# ---------- TC-05: 端口类型常量唯一 ----------

def test_port_type_constants_unique():
    """端口类型常量值唯一（含批次 A3 新增的 TRACK/COURT/POSE），且同名端口的 PortType 一致。"""
    values = [PortType.VIDEO, PortType.DURATION, PortType.CANDIDATES,
              PortType.HIGHLIGHT, PortType.REPORT,
              PortType.TRACK, PortType.COURT, PortType.POSE]
    # 各常量值唯一
    assert len(values) == len(set(values))
    # 同名端口类型一致
    p1 = Port(name="video", type=PortType.VIDEO)
    p2 = Port(name="video", type=PortType.VIDEO)
    assert p1.type == p2.type


def test_port_is_frozen():
    """Port 是 frozen dataclass，创建后不可修改。"""
    p = Port(name="test", type=PortType.VIDEO)
    with pytest.raises(AttributeError):
        p.name = "other"  # type: ignore[misc]


def test_paramspec_is_frozen():
    """ParamSpec 是 frozen dataclass，创建后不可修改。"""
    p = ParamSpec(key="x", label="X", type="int", default=1)
    with pytest.raises(AttributeError):
        p.key = "y"  # type: ignore[misc]


def test_list_specs_returns_all():
    """list_specs 返回所有已注册的 NodeSpec 列表。"""
    specs = list_specs()
    assert isinstance(specs, list)
    assert len(specs) >= 2  # 至少有 input.video 和之前注册的 test 节点
    types = {s.type for s in specs}
    assert "input.video" in types


def test_get_spec_unknown_returns_none():
    """get_spec 查找不存在的类型返回 None。"""
    assert get_spec("nonexistent.type") is None


# ---------- 全部注册节点可通过 ----------

def test_all_builtins_registered():
    """所有内置节点均可通过 get_spec 获取。"""
    builtin_types = [
        "input.video",
        "preprocess.transcode",
        "detect.candidates",
        "analyze.highlight",
        "post.filter_segments",
        "post.uniform_slices",
        "edit.concat",
        "report.technical",
        "output.artifact",
    ]
    for t in builtin_types:
        spec = get_spec(t)
        assert spec is not None, f"内置节点 {t} 未注册"
        assert spec.type == t
        assert len(spec.outputs) >= 1 or len(spec.inputs) >= 1
