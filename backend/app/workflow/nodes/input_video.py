"""输入视频节点：由执行器注入任务源视频。"""

from __future__ import annotations

from app.workflow.spec import NodeSpec, Port, PortType, register


@register(
    NodeSpec(
        type="input.video",
        label="视频输入",
        category="input",
        description="由执行器注入任务源视频",
        inputs=[],
        outputs=[Port(name="video", type=PortType.VIDEO)],
        params=[],
        stage="input",
    )
)
def run(ctx, params):
    """输出源视频路径（由执行器通过 ctx.video_path 注入）。"""
    return {"video": ctx.video_path}
