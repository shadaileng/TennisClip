"""均匀切片兜底节点：按 duration 产出 N 段且总时长覆盖全程。

批次 A1：ctx.config 为节点级深拷贝，参数覆盖直接改写、无需 try/finally 恢复。
"""

from __future__ import annotations

from app.models import HighlightResult
from app.services.segment_ops import uniform_slices
from app.workflow.spec import (
    NodeSpec,
    ParamSpec,
    Port,
    PortType,
    register,
)


@register(
    NodeSpec(
        type="post.uniform_slices",
        label="均匀切片兜底",
        category="post",
        description="按 duration 产出 N 段且总时长覆盖全程，兜底用",
        inputs=[Port(name="duration", type=PortType.DURATION)],
        outputs=[Port(name="highlight", type=PortType.HIGHLIGHT)],
        params=[
            ParamSpec(key="count", label="切片数量", type="int", default=3,
                      min=1, max=20, description="均匀切片数量"),
            ParamSpec(key="slice_seconds", label="每段时长", type="float", default=5.0,
                      min=1.0, max=30.0, description="每段目标时长（秒）"),
        ],
        stage="postprocessing",
    )
)
def run(ctx, params):
    """均匀切片产出 highlight。"""
    duration = ctx.inputs.get("duration")
    if duration is None:
        raise ValueError("节点 post.uniform_slices 的必填输入 duration 未连接")

    config = ctx.config
    # 节点级参数覆盖（ctx.config 是节点级深拷贝，直接改写不影响全局）
    config.highlight.target_duration = params["slice_seconds"]
    segments = uniform_slices(float(duration), config, count=params["count"])

    return {"highlight": HighlightResult(
        segments=segments,
        target_duration=params["slice_seconds"],
        scene_type="practice",
        reasoning="均匀切片兜底",
    )}
