"""产物投影节点：把接收到的值按端口名写回 TaskResult 既有字段。"""

from __future__ import annotations

from app.workflow.spec import (
    NodeSpec,
    Port,
    PortType,
    register,
)


@register(
    NodeSpec(
        type="output.artifact",
        label="产物输出",
        category="output",
        description="产物投影回 TaskResult，落 record_task_output",
        inputs=[
            Port(name="video", type=PortType.VIDEO, required=False),
            Port(name="report", type=PortType.REPORT, required=False),
            Port(name="highlight", type=PortType.HIGHLIGHT, required=False),
        ],
        outputs=[],
        params=[],
        stage="output",
    )
)
def run(ctx, params):
    """产物投影：把接收到的值写回 result。"""
    result = ctx.result

    # highlight_video → result.highlight_video_path
    video = ctx.inputs.get("video")
    if video is not None:
        result.highlight_video_path = str(video)

    # report → result.report / result.report_path
    tech_report = ctx.inputs.get("report")
    if tech_report is not None:
        result.report = tech_report

    # highlight → result.highlight（供 VideoPlayer 消费 segments）
    highlight = ctx.inputs.get("highlight")
    if highlight is not None:
        result.highlight = highlight

    return {}
