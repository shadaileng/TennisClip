"""高光评分排序节点：薄封装 cv_clip_score（零样本 CLIP 初筛，不依赖 LLM API）。"""

from __future__ import annotations

from app.models import HighlightResult
from app.utils import cv_clip_score
from app.workflow.spec import (
    NodeSpec,
    ParamSpec,
    Port,
    PortType,
    register,
)


@register(
    NodeSpec(
        type="post.score_highlights",
        label="高光评分排序",
        category="post",
        description="CLIP 文本-帧相似度为候选打分，零样本筛选高光（不依赖 LLM API）",
        inputs=[
            Port(name="candidates", type=PortType.CANDIDATES),
            Port(name="video", type=PortType.VIDEO),
        ],
        outputs=[Port(name="highlight", type=PortType.HIGHLIGHT)],
        params=[
            ParamSpec(key="clip_model", label="CLIP 模型", type="select", default="small",
                      options=["small", "base", "large"],
                      description="small=快 / large=准（均为 OpenAI CLIP 系列）"),
            ParamSpec(key="min_score", label="最低评分", type="float", default=0.25,
                      min=0.0, max=1.0, description="低于此相似度的候选丢弃"),
            ParamSpec(key="max_segments", label="最大段数", type="int", default=3,
                      min=1, max=20, description="评分后保留的最高分段数"),
            ParamSpec(key="highlight_phrases", label="高光描述", type="str",
                      default="exciting tennis rally, winning shot, ace serve, smash",
                      description="逗号分隔的高光文本描述（CLIP 匹配目标）"),
            ParamSpec(key="device", label="推理设备", type="select", default="auto",
                      options=["auto", "cuda", "cpu"],
                      description="auto=有 GPU 用 GPU 否则 CPU；cuda=强制 GPU；cpu=强制 CPU"),
        ],
        stage="postprocessing",
    )
)
def run(ctx, params):
    """CLIP 为候选打分 → 过滤/截断 → 输出 HighlightResult（score 写入 confidence）。"""
    candidates = ctx.inputs.get("candidates")
    video = ctx.inputs.get("video")

    if candidates is None:
        raise ValueError("节点 post.score_highlights 的必填输入 candidates 未连接")
    if video is None:
        raise ValueError("节点 post.score_highlights 的必填输入 video 未连接")

    scored = cv_clip_score.score_windows(
        video, candidates,
        model=params["clip_model"],
        phrases=params["highlight_phrases"],
        device=params.get("device", "auto"),
    )
    min_score = params["min_score"]
    top = [
        (seg, score) for (seg, score) in scored
        if score >= min_score
    ][: params["max_segments"]]

    segments = [
        seg.model_copy(update={"confidence": score}) for (seg, score) in top
    ]
    highlight = HighlightResult(
        segments=segments,
        target_duration=ctx.config.highlight.target_duration,
        scene_type="unknown",
        reasoning=(
            f"CLIP 零样本初筛：{len(segments)}/{len(candidates)} 段过阈值 "
            f"（min_score={min_score}）"
        ),
    )
    return {"highlight": highlight}
