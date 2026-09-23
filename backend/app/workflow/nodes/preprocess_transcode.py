"""预处理转码节点：封装 preprocess.preprocess + probe_video。

批次 A1：ctx.config 为节点级深拷贝，参数覆盖直接改写、无需 try/finally 恢复。
"""

from __future__ import annotations

from app.services import db_service, preprocess
from app.workflow.spec import (
    NodeSpec,
    ParamSpec,
    Port,
    PortType,
    register,
)


@register(
    NodeSpec(
        type="preprocess.transcode",
        label="预处理转码",
        category="preprocess",
        description="统一分辨率与帧率，同时落 record_task_input",
        inputs=[Port(name="video", type=PortType.VIDEO)],
        outputs=[
            Port(name="video", type=PortType.VIDEO),
            Port(name="duration", type=PortType.DURATION),
        ],
        params=[
            ParamSpec(key="height", label="目标高度", type="int", default=720,
                      min=240, max=2160, description="缩放目标高度（像素）"),
            ParamSpec(key="fps", label="目标帧率", type="int", default=30,
                      min=10, max=120, description="目标帧率（fps）"),
            ParamSpec(key="max_input_seconds", label="最大输入时长", type="int", default=300,
                      min=10, max=3600, description="超长视频截断时长（秒）"),
        ],
        stage="preprocessing",
    )
)
def run(ctx, params):
    """预处理视频，输出 video + duration。"""
    from pathlib import Path

    video_path = Path(ctx.video_path)
    config = ctx.config
    task_out = ctx.task_out

    # 节点级参数覆盖（ctx.config 是节点级深拷贝，直接改写不影响全局）
    config.video.max_input_seconds = params["max_input_seconds"]
    processed_video = preprocess.preprocess(video_path, config, work_dir=task_out)
    meta = preprocess.probe_video(processed_video)

    duration = meta.get("duration") or 60.0

    # 落库：输入元信息
    task_id = ctx.task_id
    db_service.record_task_input(
        task_id=task_id,
        video_path=video_path,
        duration_seconds=meta.get("duration"),
        width=meta.get("width"),
        height=meta.get("height"),
        fps=meta.get("fps"),
        level=ctx.level,
    )

    return {"video": processed_video, "duration": duration}
