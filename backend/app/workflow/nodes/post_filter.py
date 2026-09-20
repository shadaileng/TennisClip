"""后处理过滤节点：剔除低置信度与黑名单标签段。"""

from __future__ import annotations

from app.services.segment_ops import EXCLUDED_LABELS, clamp_segments, exclude_prep
from app.workflow.spec import (
    NodeSpec,
    ParamSpec,
    Port,
    PortType,
    register,
)


@register(
    NodeSpec(
        type="post.filter_segments",
        label="片段过滤",
        category="post",
        description="剔除低置信度与黑名单标签段",
        inputs=[Port(name="highlight", type=PortType.HIGHLIGHT)],
        outputs=[Port(name="highlight", type=PortType.HIGHLIGHT)],
        params=[
            ParamSpec(key="min_confidence", label="最低置信度", type="float", default=0.6,
                      min=0.0, max=1.0, description="低于此置信度的段丢弃"),
            ParamSpec(key="exclude_labels", label="排除标签", type="str",
                      default=",".join(sorted(EXCLUDED_LABELS)),
                      description="逗号分隔的排除标签列表"),
            ParamSpec(key="min_duration", label="最小时长", type="float", default=0.0,
                      min=0.0, max=30.0, description="低于此时长的段丢弃（0=不限制）"),
        ],
        stage="postprocessing",
    )
)
def run(ctx, params):
    """过滤 highlight 中的无效段。"""
    highlight = ctx.inputs.get("highlight")
    if highlight is None:
        raise ValueError("节点 post.filter_segments 的必填输入 highlight 未连接")

    # 解析排除标签
    raw_labels = params.get("exclude_labels", "")
    excluded = {l.strip() for l in raw_labels.split(",") if l.strip()} if raw_labels else EXCLUDED_LABELS

    # 最小置信度过滤
    min_conf = params["min_confidence"]
    highlight.segments = [
        s for s in highlight.segments
        if s.confidence >= min_conf
    ]

    # 排除标签过滤
    highlight.segments = [
        s for s in highlight.segments
        if s.label not in excluded
    ]

    # 最小时长过滤
    min_dur = params["min_duration"]
    if min_dur > 0:
        highlight.segments = [
            s for s in highlight.segments
            if (s.end - s.start) >= min_dur
        ]

    return {"highlight": highlight}
