"""姿态估计节点：薄封装 cv_pose。"""

from __future__ import annotations

from app.utils import cv_pose
from app.workflow.spec import (
    NodeSpec,
    ParamSpec,
    Port,
    PortType,
    register,
)


@register(
    NodeSpec(
        type="detect.pose",
        label="姿态估计",
        category="detect",
        description="MediaPipe 提取球员骨骼关键点序列，供击球分类与技术分析",
        inputs=[Port(name="video", type=PortType.VIDEO)],
        outputs=[Port(name="pose", type=PortType.POSE)],
        params=[
            ParamSpec(key="max_people", label="最大人数", type="int", default=2,
                      min=1, max=4, description="同时追踪的最大人数"),
            ParamSpec(key="fps", label="采样帧率", type="float", default=15.0,
                      min=1.0, max=60.0, description="姿态采样帧率（降计算量）"),
        ],
        stage="detecting",
        on_failure="skip",  # GPU/依赖易失败，降级不拖垮任务（方案 12 · 2.8）
    )
)
def run(ctx, params):
    """提取姿态关键点序列，输出 pose dict。"""
    video = ctx.inputs.get("video")
    if video is None:
        raise ValueError("节点 detect.pose 的必填输入 video 未连接")

    pose = cv_pose.extract_poses(
        video,
        max_people=params["max_people"],
        fps=params["fps"],
    )
    return {"pose": pose}
