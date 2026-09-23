"""网球领域知识模块：规则 + 生物力学知识库（纯数据，供 Prompt 注入）。

方案 12 · 阶段 1 内部模块（Step 1.2）：
- 被 prompts.highlight_analysis.build_highlight_prompt 引用（标签判据 / 视觉判别）；
- 被 prompts.highlight_analysis.build_report_prompt 引用（报告领域知识，按档位缩放）。

设计原则：纯数据 + 轻量格式化函数，无外部依赖，不散落在 service 内。
深度档位（basic/standard/expert）按"每组注入章节数"缩放：
  basic=判分规则+动力链（最常用）；standard=+计分/发球动作；expert=全部六章。
"""

from __future__ import annotations

from typing import Dict, List

# 规则知识（章节按"核心 → 扩展"排序，档位截取前 N 章）
RULES: Dict[str, List[str]] = {
    "判分规则": [
        "ace：发球落入有效区且接发球员未触及，或触及但未将球打过网",
        "winner（制胜分）：击球落地后对手未触及，或触及但未成功回击",
        "let：发球触网后落入正确发球区，重新发球（不计分）",
        "fault：发球下网、落入错误区或脚误；两次发球失误即双误直接送分",
        "out：球落点出界、先落地两次或触碰非球网固定装置",
    ],
    "计分与胜负": [
        "局内计分 15/30/40，deuce 后须连赢两分（占先 + 得分）",
        "抢七：6-6 后进行，先得 7 分且领先 2 分者胜该盘",
        "盘数：常规三盘两胜制，大满贯男单五盘三胜制",
        "每局结束后交换发球方，单数局结束后双方交换场地",
    ],
    "场地与器材": [
        "单打场地 23.77m × 8.23m，双打宽 10.97m；发球区长 6.40m",
        "网中央高 0.914m，网柱处高 1.07m",
        "球速与弹跳：草地 > 硬地 > 红土（红土弹跳高且减速明显）",
    ],
}

# 生物力学知识（章节按"核心 → 扩展"排序，与 RULES 同档位截取）
BIOMECHANICS: Dict[str, List[str]] = {
    "动力链": [
        "发力顺序：蹬地 → 转髋 → 转肩 → 肘腕鞭打，力量自下而上传递",
        "髋肩分离（X-factor）：躯干扭转储能，是正手/发球拍头速度的主要来源",
        "鞭打末端加速：大臂制动后前臂旋内 / 屈腕将动量传至拍头",
        "击球点：位于身体侧前方时力臂与重心转移效率最高",
        "重心转移：随挥结束时重心应落在前脚，避免后仰击球",
    ],
    "发球动作": [
        "奖杯姿势：抛球后膝屈、拍头竖起、肩线倾斜，储能待发",
        "蹬伸：下肢爆发向上，将地面反作用力传入躯干",
        "搔背（racquet drop）：拍头下垂于背后，拉长挥拍加速路径",
        "内旋（pronation）：前臂旋内使拍面正对击球方向，产生拍头速度峰值",
        "落地：击球后前脚落入场内，衔接下一拍准备",
    ],
    "移动与制动": [
        "分腿垫步：对手触球瞬间双脚轻跳落地，预加载腿部弹簧",
        "第一步爆发：髋膝踝三关节伸展产生启动加速度",
        "制动：离心收缩吸收动能，为变向提供支点",
        "回位：击球后沿最短路径回中，优先保护直线空当",
    ],
}

# 深度档位 → 每组（RULES / BIOMECHANICS）注入的章节数
_DEPTH_SECTIONS: Dict[str, int] = {"basic": 1, "standard": 2, "expert": 3}


def domain_knowledge(depth: str = "standard") -> str:
    """按知识深度档返回领域知识文本块（规则 + 生物力学）。

    basic：判分规则 + 动力链；standard：+ 计分与胜负 + 发球动作；expert：全量六章。
    """
    n = _DEPTH_SECTIONS.get(depth, _DEPTH_SECTIONS["standard"])
    parts: List[str] = []
    for group in (RULES, BIOMECHANICS):
        for title, items in list(group.items())[:n]:
            lines = "\n".join(f"- {item}" for item in items)
            parts.append(f"【{title}】\n{lines}")
    return "\n".join(parts)


def label_reference() -> str:
    """高光标签的规则判据（ace/winner/let/fault/out），供高光识别 Prompt 引用。"""
    lines = "\n".join(f"- {item}" for item in RULES["判分规则"])
    return f"标签判据（网球规则依据）：\n{lines}"
