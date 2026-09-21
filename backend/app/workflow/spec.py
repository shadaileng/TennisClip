"""节点契约：端口类型、参数 Schema、节点规范、注册表。

节点是工作流的基本单元，每个节点有类型化端口（输入/输出）和参数 Schema。
前端表单由参数 Schema 驱动渲染；校验规则只在后端执行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


# ---------- 端口类型常量 ----------

class PortType:
    """端口类型：连线合法性的唯一判据。"""
    VIDEO = "video"
    DURATION = "duration"
    CANDIDATES = "candidates"
    HIGHLIGHT = "highlight"
    REPORT = "report"


# ---------- 端口与参数 ----------

@dataclass(frozen=True)
class Port:
    """节点端口：名称 + 类型。"""
    name: str
    type: str
    required: bool = True


@dataclass(frozen=True)
class ParamSpec:
    """参数规范：驱动前端表单渲染与校验。"""
    key: str
    label: str
    type: str  # str | int | float | bool | select
    default: Any
    options: list | None = None
    min: float | None = None
    max: float | None = None
    description: str = ""


# ---------- 节点规范 ----------

@dataclass(frozen=True)
class NodeSpec:
    """节点规范：类型、端口、参数、阶段、标记。"""
    type: str
    label: str
    category: str  # input | preprocess | detect | analyze | post | edit | report | output
    description: str
    inputs: list[Port]
    outputs: list[Port]
    params: list[ParamSpec]
    stage: str  # 进度上报用的 stage 名
    optional: bool = False  # 失败不致命（如 report）
    expensive: bool = False  # LLM 调用等高成本节点


# ---------- 注册表 ----------

_SPEC_REGISTRY: dict[str, NodeSpec] = {}
_FN_REGISTRY: dict[str, Callable] = {}


def _get_available_models() -> list[str]:
    """从 DB 服务商表获取所有启用模型，返回 'provider_name/model_id' 格式列表。"""
    try:
        from app.services import db_service
        providers = db_service.list_providers()
        models: list[str] = []
        for p in providers:
            if not p.get("enabled", True):
                continue
            pname = p.get("name", "")
            for m in (p.get("models") or []):
                label = f"{pname}/{m}" if pname else m
                if label not in models:
                    models.append(label)
        return models
    except Exception:  # noqa: BLE001
        return []


def register(spec: NodeSpec) -> Callable:
    """注册节点：装饰器，绑定 spec 与执行函数。"""
    def decorator(fn: Callable) -> Callable:
        if spec.type in _SPEC_REGISTRY:
            raise ValueError(f"节点类型重复注册：{spec.type}")
        _SPEC_REGISTRY[spec.type] = spec
        _FN_REGISTRY[spec.type] = fn
        return fn
    return decorator


def get_spec(node_type: str) -> Optional[NodeSpec]:
    """按 type 获取 NodeSpec；不存在返回 None。"""
    return _SPEC_REGISTRY.get(node_type)


def get_fn(node_type: str) -> Optional[Callable]:
    """按 type 获取节点执行函数；不存在返回 None。"""
    return _FN_REGISTRY.get(node_type)


def list_specs() -> list[NodeSpec]:
    """返回所有已注册的 NodeSpec 列表。"""
    return list(_SPEC_REGISTRY.values())


def build_schema() -> list[dict]:
    """构建节点目录 Schema（前端渲染依据），可 JSON 序列化。"""
    # 动态获取可用模型列表（从 DB 服务商表）
    _available_models = _get_available_models()
    schema = []
    for spec in _SPEC_REGISTRY.values():
        node = {
            "type": spec.type,
            "label": spec.label,
            "category": spec.category,
            "description": spec.description,
            "inputs": [{"name": p.name, "type": p.type, "required": p.required} for p in spec.inputs],
            "outputs": [{"name": p.name, "type": p.type} for p in spec.outputs],
            "params": [
                {
                    "key": p.key,
                    "label": p.label,
                    "type": p.type,
                    "default": p.default,
                    "options": _available_models if (p.key == "model" and not p.options) else p.options,
                    "min": p.min,
                    "max": p.max,
                    "description": p.description,
                }
                for p in spec.params
            ],
            "stage": spec.stage,
            "optional": spec.optional,
            "expensive": spec.expensive,
        }
        schema.append(node)
    return schema


def resolve_params(param_specs: list[ParamSpec], values: dict) -> dict[str, Any]:
    """按 ParamSpec 校验并填充参数：缺省值 → 类型/范围/选项校验。

    返回完整参数字典；校验失败抛 ValueError。
    """
    result: dict[str, Any] = {}
    for spec in param_specs:
        val = values.get(spec.key, spec.default)

        # 类型归一化
        if spec.type == "int" and not isinstance(val, int):
            try:
                val = int(val)
            except (TypeError, ValueError):
                raise ValueError(f"参数 {spec.key} 期望 int 类型，实际为 {type(val).__name__}")

        if spec.type == "float" and not isinstance(val, (int, float)):
            try:
                val = float(val)
            except (TypeError, ValueError):
                raise ValueError(f"参数 {spec.key} 期望 float 类型，实际为 {type(val).__name__}")

        if spec.type == "bool" and not isinstance(val, bool):
            val = str(val).lower() in ("true", "1", "yes")

        # 选项校验（空字符串始终允许，表示使用默认值）
        if spec.type == "select" and spec.options is not None:
            # 动态解析 model 参数的可用选项（与 build_schema 保持一致）
            check_options = spec.options
            if spec.key == "model" and not spec.options:
                check_options = _get_available_models()
            if val != "" and val not in check_options:
                raise ValueError(f"参数 {spec.key} 取值非法：{val}，应为 {check_options}")

        # 范围校验（int/float）
        if spec.type in ("int", "float") and isinstance(val, (int, float)):
            if spec.min is not None and val < spec.min:
                raise ValueError(f"参数 {spec.key} 取值非法：{val}，最小值为 {spec.min}")
            if spec.max is not None and val > spec.max:
                raise ValueError(f"参数 {spec.key} 取值非法：{val}，最大值为 {spec.max}")

        result[spec.key] = val
    return result
