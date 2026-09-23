"""击球类型分类节点：薄封装 cv_stroke_cls，回写 segment.label。

批次 A2：函数式传递——用 model_copy 生成新段列表，禁止原地修改上游 highlight。
pose 输入 required=False：未连线时仅 cnn 分类器可用，dtw 会抛可修复的连线错误。
"""

from __future__ import annotations

from app.utils import cv_stroke_cls
from app.workflow.spec import (
    NodeSpec,
    ParamSpec,
    Port,
    PortType,
    register,
)


@register(
    NodeSpec(
        type="post.classify_strokes",
        label="击球类型分类",
        category="post",
        description="基于姿态关键点 + 片段帧的轻量 CNN 分类击球类型，回写 segment.label",
        inputs=[
            Port(name="highlight", type=PortType.HIGHLIGHT),
            Port(name="pose", type=PortType.POSE, required=False),  # 无姿态降级纯视觉分类
        ],
        outputs=[Port(name="highlight", type=PortType.HIGHLIGHT)],
        params=[
            ParamSpec(key="classifier", label="分类器", type="select", default="cnn",
                      options=["cnn", "dtw"], description="cnn=3D CNN / dtw=姿态动态时间规整"),
            ParamSpec(key="min_confidence", label="最低置信度", type="float", default=0.5,
                      min=0.0, max=1.0, description="低于此置信度保留原 label 不覆盖"),
        ],
        stage="postprocessing",
    )
)
def run(ctx, params):
    """分类各高光段击球类型，置信度达标才回写 label，输出新 highlight。"""
    highlight = ctx.inputs.get("highlight")
    if highlight is None:
        raise ValueError("节点 post.classify_strokes 的必填输入 highlight 未连接")
    pose = ctx.inputs.get("pose")

    min_conf = params["min_confidence"]
    segments = list(highlight.segments)
    if segments:
        preds = cv_stroke_cls.classify_segments(
            ctx.video_path, segments,
            pose=pose,
            classifier=params["classifier"],
        )
        updated = []
        for idx, seg in enumerate(segments):
            pred = preds.get(idx)
            if pred is not None and pred[1] >= min_conf:
                # 仅回写 label（方案 4.3.3⑥）：置信度判定已过阈值，原 confidence 保留
                seg = seg.model_copy(update={"label": pred[0]})
            updated.append(seg)
        segments = updated

    return {"highlight": highlight.model_copy(update={"segments": segments})}
