"""球场关键点检测节点：薄封装 cv_court。"""

from __future__ import annotations

from app.utils import cv_court
from app.workflow.spec import (
    NodeSpec,
    ParamSpec,
    Port,
    PortType,
    register,
)


@register(
    NodeSpec(
        type="detect.court",
        label="球场关键点检测",
        category="detect",
        description="检测 14 个球场关键点，建立像素→球场物理坐标映射",
        inputs=[Port(name="video", type=PortType.VIDEO)],
        outputs=[Port(name="court", type=PortType.COURT)],
        params=[
            ParamSpec(key="refine", label="关键点精修", type="bool", default=True,
                      description="是否用传统 CV 精修 + 单应性校正（+精度，-速度）"),
            ParamSpec(key="interval", label="检测间隔帧", type="int", default=30,
                      min=1, max=300, description="每隔多少帧检测一次（球场静止，无需逐帧）"),
            ParamSpec(key="device", label="推理设备", type="select", default="auto",
                      options=["auto", "cuda", "cpu"],
                      description="auto=有 GPU 用 GPU 否则 CPU；cuda=强制 GPU；cpu=强制 CPU"),
        ],
        stage="detecting",
        on_failure="skip",  # GPU/权重易失败，降级不拖垮任务（方案 12 · 2.8）
    )
)
def run(ctx, params):
    """检测球场关键点，输出 court dict。"""
    video = ctx.inputs.get("video")
    if video is None:
        raise ValueError("节点 detect.court 的必填输入 video 未连接")

    court = cv_court.detect_court(
        video,
        refine=params["refine"],
        interval=params["interval"],
        device=params.get("device", "auto"),
    )
    return {"court": court}
