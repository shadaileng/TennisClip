"""预处理转码节点：封装 preprocess.preprocess + probe_video。

批次 A1：ctx.config 为节点级深拷贝，参数覆盖直接改写、无需 try/finally 恢复。
批次 C2：输入元信息落库改由执行器按 NodeSpec.records_input 统一执行，
         probe 元信息经 "meta" 输出端口透出——节点保持纯函数、不直调 db_service。
"""

from __future__ import annotations

from app.services import preprocess
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
        description="统一分辨率与帧率，输出 video/duration/meta（输入元信息由执行器按 records_input 落库）",
        inputs=[Port(name="video", type=PortType.VIDEO)],
        outputs=[
            Port(name="video", type=PortType.VIDEO),
            Port(name="duration", type=PortType.DURATION),
            Port(name="meta", type=PortType.META, required=False),
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
        records_input=True,
    )
)
def run(ctx, params):
    """预处理视频，输出 video + duration + meta（probe 元信息）。"""
    from pathlib import Path

    video_path = Path(ctx.video_path)
    config = ctx.config
    task_out = ctx.task_out

    # 节点级参数覆盖（ctx.config 是节点级深拷贝，直接改写不影响全局）
    config.video.max_input_seconds = params["max_input_seconds"]
    # 目标高度/帧率：ParamSpec height/fps 经 config.video 透传给 preprocess，
    # preprocess 读 resolution_height（默认 720）与 fps，不再硬编码 720p。
    config.video.resolution = f"{params['height']}p"
    config.video.fps = params["fps"]
    processed_video = preprocess.preprocess(video_path, config, work_dir=task_out)
    meta = preprocess.probe_video(processed_video)

    duration = meta.get("duration") or 60.0

    # 批次 C2：输入元信息落库由执行器按 records_input 处理，这里只经 meta 端口透出
    return {"video": processed_video, "duration": duration, "meta": meta}
