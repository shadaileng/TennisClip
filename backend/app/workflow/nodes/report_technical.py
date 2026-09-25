"""技术分析报告节点：封装 report.generate_report。

批次 C2：产物落库改由执行器按 NodeSpec.persists=(("report","report"),) 统一执行
（非路径端口值按约定查 task_out/report.json，文件不存在跳过）——节点保持纯函数。
"""

from __future__ import annotations

from app.services import report
from app.utils.logger import get_logger
from app.workflow.spec import (
    NodeSpec,
    ParamSpec,
    Port,
    PortType,
    register,
)

logger = get_logger(__name__)


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
        persists=(("report", "report"),),
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

    # all 档位不生成报告：提交档位 level=all 或上游 all_highlights 标记任一命中即跳过
    #（API 契约 level=all = 仅剪辑拼接不生成报告；自定义图内 params 固化，须以提交档位兜底）
    if highlight.all_highlights or getattr(ctx, "level", "") == "all":
        logger.info("report: level=all 档位，跳过技术分析报告 task_id={}", getattr(ctx, "task_id", ""))
        return {"report": None}

    config = ctx.config
    level = params["level"]
    knowledge_level = params.get("knowledge_level") or "standard"
    task_out = ctx.task_out
    report_file = task_out / "report.json"

    tech_report = report.generate_report(
        video, highlight, config, level=level, out_path=report_file,
        knowledge_level=knowledge_level,
        task_id=getattr(ctx, "task_id", ""),
    )

    # 批次 C2：产物落库由执行器按 persists=("report","report") 统一执行
    #（非路径端口值按约定查 task_out/report.json，文件不存在跳过）
    return {"report": tech_report}
