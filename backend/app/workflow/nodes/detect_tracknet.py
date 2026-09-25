"""TrackNet 球追踪节点：薄封装 cv_tracknet + event_detect 轨迹聚合。

批次 A1：ctx.config 为执行器注入的节点级深拷贝，cluster_gap 写入天然隔离。
"""

from __future__ import annotations

from app.services import event_detect
from app.utils import cv_tracknet
from app.workflow.spec import (
    NodeSpec,
    ParamSpec,
    Port,
    PortType,
    register,
)


@register(
    NodeSpec(
        type="detect.tracknet",
        label="TrackNet 球追踪",
        category="detect",
        description="TrackNet 深度网络追踪网球轨迹，聚合为候选窗口（替换/对比 ffmpeg 音频信号）",
        inputs=[
            Port(name="video", type=PortType.VIDEO),
            Port(name="duration", type=PortType.DURATION),
        ],
        outputs=[
            Port(name="track", type=PortType.TRACK),
            Port(name="candidates", type=PortType.CANDIDATES),
        ],
        params=[
            ParamSpec(key="confidence", label="置信度阈值", type="float", default=0.5,
                      min=0.0, max=1.0, description="低于此置信度的球位置丢弃"),
            ParamSpec(key="cluster_gap", label="聚类间隔", type="float", default=4.0,
                      min=0.5, max=15.0, description="相邻击球聚为回合的间隔上限（秒）"),
            ParamSpec(key="top_n", label="候选上限", type="int", default=5,
                      min=0, max=50, description="候选窗口上限；0=放开"),
            ParamSpec(key="frame_stride", label="抽帧步长", type="int", default=1,
                      min=1, max=5, description="追踪时的帧采样步长（降 GPU 负载）"),
            ParamSpec(key="device", label="推理设备", type="select", default="auto",
                      options=["auto", "cuda", "cpu"],
                      description="auto=有 GPU 用 GPU 否则 CPU；cuda=强制 GPU；cpu=强制 CPU"),
        ],
        stage="detecting",
        on_failure="skip",  # GPU/权重易失败，降级不拖垮任务（方案 12 · 2.8）
    )
)
def run(ctx, params):
    """追踪网球并聚合为候选窗口，输出 track + candidates。"""
    video = ctx.inputs.get("video")
    duration = ctx.inputs.get("duration")

    if video is None:
        raise ValueError("节点 detect.tracknet 的必填输入 video 未连接")
    if duration is None:
        raise ValueError("节点 detect.tracknet 的必填输入 duration 未连接")

    config = ctx.config
    # 节点级参数覆盖（深拷贝隔离）：聚类间隔复用 event_detect 的回合聚合
    config.highlight.hit_cluster_gap_seconds = params["cluster_gap"]

    track = cv_tracknet.track_video(
        video, float(duration),
        confidence=params["confidence"],
        frame_stride=params["frame_stride"],
        task_id=getattr(ctx, "task_id", ""),
        device=params.get("device", "auto"),
    )
    effective_top_n = None if params["top_n"] == 0 else params["top_n"]
    candidates = event_detect.track_to_candidates(
        track, float(duration), config.highlight, top_n=effective_top_n,
    )
    return {"track": track, "candidates": candidates}
