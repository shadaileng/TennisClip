"""球员检测追踪节点：薄封装 cv_player + event_detect 分数聚合。"""

from __future__ import annotations

from app.services import event_detect
from app.utils import cv_player
from app.workflow.spec import (
    NodeSpec,
    ParamSpec,
    Port,
    PortType,
    register,
)


@register(
    NodeSpec(
        type="detect.player",
        label="球员检测追踪",
        category="detect",
        description="YOLOv8 检测球员 + 追踪 ID，按运动强度聚合为候选窗口",
        inputs=[
            Port(name="video", type=PortType.VIDEO),
            Port(name="duration", type=PortType.DURATION),
        ],
        outputs=[Port(name="candidates", type=PortType.CANDIDATES)],
        params=[
            ParamSpec(key="model_size", label="模型规格", type="select", default="n",
                      options=["n", "s", "m"], description="YOLOv8 规格：n 最快 / m 最准"),
            ParamSpec(key="motion_percentile", label="运动分位阈值", type="float", default=80,
                      min=50, max=99, description="运动强度分位阈值（%）"),
            ParamSpec(key="top_n", label="候选上限", type="int", default=5,
                      min=0, max=50, description="候选窗口上限；0=放开"),
        ],
        stage="detecting",
        on_failure="skip",  # GPU/权重易失败，降级不拖垮任务（方案 12 · 2.8）
    )
)
def run(ctx, params):
    """球员运动强度 → 候选窗口，输出 candidates。"""
    video = ctx.inputs.get("video")
    duration = ctx.inputs.get("duration")

    if video is None:
        raise ValueError("节点 detect.player 的必填输入 video 未连接")
    if duration is None:
        raise ValueError("节点 detect.player 的必填输入 duration 未连接")

    scores, step = cv_player.compute_motion_scores(
        video, float(duration), model_size=params["model_size"],
    )
    effective_top_n = None if params["top_n"] == 0 else params["top_n"]
    candidates = event_detect.scores_to_candidates(
        scores, step, float(duration), ctx.config.highlight,
        percentile=params["motion_percentile"], top_n=effective_top_n,
    )
    return {"candidates": candidates}
