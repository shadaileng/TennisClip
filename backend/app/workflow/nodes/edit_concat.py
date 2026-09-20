"""剪辑合成节点：封装 video_editor.edit_highlight_video。"""

from __future__ import annotations

from app.services import db_service, video_editor
from app.workflow.spec import (
    NodeSpec,
    ParamSpec,
    Port,
    PortType,
    register,
)


@register(
    NodeSpec(
        type="edit.concat",
        label="剪辑合成",
        category="edit",
        description="按时间戳拼接高光片段，渲染为标准集锦视频",
        inputs=[
            Port(name="video", type=PortType.VIDEO),
            Port(name="highlight", type=PortType.HIGHLIGHT),
        ],
        outputs=[
            Port(name="video", type=PortType.VIDEO),
            Port(name="highlight", type=PortType.HIGHLIGHT),
        ],
        params=[
            ParamSpec(key="target_duration", label="目标时长", type="int", default=15,
                      min=5, max=120, description="集锦目标时长（秒）"),
            ParamSpec(key="all_mode", label="全量模式", type="bool", default=False,
                      description="遍历整段、拼接全部高光回合"),
        ],
        stage="editing",
    )
)
def run(ctx, params):
    """剪辑合成高光集锦视频。"""
    video = ctx.inputs.get("video")
    highlight = ctx.inputs.get("highlight")

    if video is None:
        raise ValueError("节点 edit.concat 的必填输入 video 未连接")
    if highlight is None:
        raise ValueError("节点 edit.concat 的必填输入 highlight 未连接")

    config = ctx.config

    # all_mode 覆盖
    if params["all_mode"]:
        highlight.all_highlights = True
        highlight.target_duration = params["target_duration"]

    hl_video = video_editor.edit_highlight_video(video, highlight, config)

    # 落库：输出 - 集锦视频
    task_id = ctx.task_id
    db_service.record_task_output(
        task_id, "highlight_video", str(hl_video),
        hl_video.stat().st_size / (1024 * 1024),
        target_duration=config.highlight.target_duration,
    )

    return {"video": hl_video, "highlight": highlight}
