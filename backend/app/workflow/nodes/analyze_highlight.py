"""LLM 高光识别节点：封装 highlight.find_highlights。"""

from __future__ import annotations

from app.services import highlight as highlight_service
from app.workflow.spec import (
    NodeSpec,
    ParamSpec,
    Port,
    PortType,
    register,
)


@register(
    NodeSpec(
        type="analyze.highlight",
        label="LLM 高光识别",
        category="analyze",
        description="调用 LLM 多模态理解，输出结构化高光时间戳",
        inputs=[
            Port(name="video", type=PortType.VIDEO),
            Port(name="duration", type=PortType.DURATION),
            Port(name="candidates", type=PortType.CANDIDATES, required=False),
        ],
        outputs=[Port(name="highlight", type=PortType.HIGHLIGHT)],
        params=[
            ParamSpec(key="level", label="分析层级", type="select", default="intermediate",
                      options=["beginner", "intermediate", "professional", "all"],
                      description="分析深度"),
            ParamSpec(key="analysis_mode", label="分析模式", type="select", default="frame",
                      options=["frame", "video"],
                      description="媒体输入策略"),
            ParamSpec(key="model", label="模型", type="select", default="",
                      options=[],
                      description="选择 LLM 模型（空=使用默认配置）"),
            ParamSpec(key="prompt_variant", label="Prompt 变体", type="select", default="standard",
                      options=["standard", "strict", "teaching"],
                      description="识别口径：standard=标准 / strict=宁缺毋滥 / teaching=保留教学素材"),
        ],
        stage="highlighting",
        expensive=True,
    )
)
def run(ctx, params):
    """LLM 高光识别，输出 HighlightResult。"""
    video = ctx.inputs.get("video")
    duration = ctx.inputs.get("duration")
    candidates = ctx.inputs.get("candidates")

    if video is None:
        raise ValueError("节点 analyze.highlight 的必填输入 video 未连接")
    if duration is None:
        raise ValueError("节点 analyze.highlight 的必填输入 duration 未连接")

    config = ctx.config
    level = params["level"]
    analysis_mode = params["analysis_mode"]
    model = params.get("model") or None
    prompt_variant = params.get("prompt_variant") or "standard"

    hl = highlight_service.find_highlights(
        video, config, float(duration), level=level, analysis_mode=analysis_mode, model=model,
        candidates=candidates, prompt_variant=prompt_variant,
        task_id=getattr(ctx, "task_id", ""),
    )

    return {"highlight": hl}
