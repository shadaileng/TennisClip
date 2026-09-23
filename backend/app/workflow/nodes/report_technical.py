"""技术分析报告节点：封装 report.generate_report。"""

from __future__ import annotations

from pathlib import Path

from app.services import db_service, report
from app.workflow.spec import (
    NodeSpec,
    ParamSpec,
    Port,
    PortType,
    register,
)


@register(
    NodeSpec(
        type="report.technical",
        label="技术分析报告",
        category="report",
        description="基于高光结果 + LLM 深度分析，输出分层教学建议",
        inputs=[
            Port(name="video", type=PortType.VIDEO),
            Port(name="highlight", type=PortType.HIGHLIGHT),
        ],
        outputs=[Port(name="report", type=PortType.REPORT)],
        params=[
            ParamSpec(key="level", label="分析层级", type="select", default="intermediate",
                      options=["beginner", "intermediate", "professional"],
                      description="报告分析深度"),
            ParamSpec(key="knowledge_level", label="知识库档位", type="select", default="standard",
                      options=["basic", "standard", "expert"],
                      description="教学知识库深度：basic=要点 / standard=+规则 / expert=全量"),
        ],
        stage="reporting",
        optional=True,  # 失败不致命，保留集锦
        expensive=True,
    )
)
def run(ctx, params):
    """生成技术分析报告。"""
    video = ctx.inputs.get("video")
    highlight = ctx.inputs.get("highlight")

    if video is None:
        raise ValueError("节点 report.technical 的必填输入 video 未连接")
    if highlight is None:
        raise ValueError("节点 report.technical 的必填输入 highlight 未连接")

    # all 档位不生成报告
    if highlight.all_highlights:
        return {"report": None}

    config = ctx.config
    level = params["level"]
    knowledge_level = params.get("knowledge_level") or "standard"
    task_out = ctx.task_out
    report_file = task_out / "report.json"

    tech_report = report.generate_report(
        video, highlight, config, level=level, out_path=report_file,
        knowledge_level=knowledge_level,
    )

    # 落库：输出 - 报告文件
    if report_file.exists():
        db_service.record_task_output(
            ctx.task_id, "report", str(report_file),
            report_file.stat().st_size / (1024 * 1024),
        )

    return {"report": tech_report}
