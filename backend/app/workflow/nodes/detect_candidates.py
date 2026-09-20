"""信号候选定位节点：封装 event_detect.detect_candidates。"""

from __future__ import annotations

from app.services import event_detect
from app.workflow.spec import (
    NodeSpec,
    ParamSpec,
    Port,
    PortType,
    register,
)


@register(
    NodeSpec(
        type="detect.candidates",
        label="信号候选定位",
        category="detect",
        description="基于 ffmpeg 信号定位候选时间窗口",
        inputs=[
            Port(name="video", type=PortType.VIDEO),
            Port(name="duration", type=PortType.DURATION),
        ],
        outputs=[Port(name="candidates", type=PortType.CANDIDATES)],
        params=[
            ParamSpec(key="mode", label="信号模式", type="select", default="audio_motion",
                      options=["audio", "motion", "scene", "audio_motion", "auto"],
                      description="可插拔信号管线"),
            ParamSpec(key="top_n", label="候选上限", type="int", default=5,
                      min=1, max=50, description="候选窗口保留上限；0=放开上限"),
        ],
        stage="detecting",
    )
)
def run(ctx, params):
    """检测候选窗口，输出 candidates 列表。"""
    video = ctx.inputs.get("video")
    duration = ctx.inputs.get("duration")

    if video is None:
        raise ValueError("节点 detect.candidates 的必填输入 video 未连接")
    if duration is None:
        raise ValueError("节点 detect.candidates 的必填输入 duration 未连接")

    config = ctx.config
    # 节点级参数覆盖
    original_mode = config.highlight.candidate_mode
    original_top_n = config.highlight.candidate_top_n
    try:
        config.highlight.candidate_mode = params["mode"]
        config.highlight.candidate_top_n = params["top_n"]
        # top_n=0 表示放开上限（None）
        effective_top_n = None if params["top_n"] == 0 else params["top_n"]
        candidates = event_detect.detect_candidates(
            video, config, float(duration), top_n=effective_top_n
        )
    finally:
        config.highlight.candidate_mode = original_mode
        config.highlight.candidate_top_n = original_top_n

    return {"candidates": candidates}
