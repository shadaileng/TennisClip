"""报告模板（分层教学知识库注入点）。

当前报告 Prompt 由 prompts.highlight_analysis.build_report_prompt 统一生成；
本模块预置各层级的"教学要点库"，供后续版本替换为可配置的知识库文件（YAML/JSON），
实现需求文档 4.1 风险3 规避方案中的"植入网球专业教学知识库"。
"""

from __future__ import annotations

from typing import Dict, List

# 各层级动作教学要点（示例种子数据，后续从外部知识库文件加载）
TEACHING_KNOWLEDGE: Dict[str, List[str]] = {
    "beginner": [
        "握拍：大陆式/东方式握拍选择与检查",
        "正手：转体-引拍-前冲击球的基本发力顺序",
        "步伐：分腿垫步 + 交叉步",
    ],
    "intermediate": [
        "正手发力链：髋-肩-肘-腕时序",
        "二发：抛球点前移与节奏变化",
        "截击：引拍幅度与击球点前移",
    ],
    "professional": [
        "发球：内旋启动时机与拍头速度",
        "反手：双手反拍的重心转移与引拍角度",
        "相持：击球弧线控制与落点选择",
    ],
}


def knowledge_for(level: str) -> List[str]:
    """返回指定层级的教学要点列表。"""
    return TEACHING_KNOWLEDGE.get(level, TEACHING_KNOWLEDGE["intermediate"])
