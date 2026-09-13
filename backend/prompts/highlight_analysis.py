"""领域 Prompt 模板：网球教学知识库注入点。

设计原则（需求文档 2.3）：
- 固定执行逻辑 + 强制 JSON 结构化输出；
- 按用户层级（beginner/intermediate/professional）差异化分析口径；
- 复杂场景（高速击球/遮挡/暗光）判别规则显式写入 Prompt（风险1 规避）。
"""

from __future__ import annotations

from app.models import HighlightResult

_COMPLEX_SCENE_RULES = """
复杂场景判别规则：
- 高速击球（球速 > 120km/h）：以击球瞬间身体姿态为准，忽略球体模糊；
- 遮挡画面：结合前后 0.5s 帧的球路轨迹插值判断；
- 暗光画面：降低该片段置信度上限至 0.8，并在 reasoning 中说明。
"""

_LEVEL_PROFILES = {
    "beginner": "面向入门学员：术语通俗化，建议以基础动作纠正为主，避免高级训练术语。",
    "intermediate": "面向进阶学员：使用标准网球教学术语，分析正反手/发球/步伐的优缺点并给出针对性训练方案。",
    "professional": "面向专业选手/教练：深入发力链（髋-肩-肘-腕）、生物力学细节，建议可直接用于训练计划。",
}


def build_highlight_prompt(level: str = "intermediate", duration_seconds: float | None = None) -> str:
    """高光识别 + 双任务并行 Prompt。"""
    profile = _LEVEL_PROFILES.get(level, _LEVEL_PROFILES["intermediate"])
    dur_note = f"\n视频总时长约 {duration_seconds:.1f} 秒。" if duration_seconds else ""
    return f"""你是网球视频分析 Agent。请观看视频，完成以下任务：

任务1（高光识别）：筛选 ACE 球、多拍相持、绝杀得分等高光回合，输出起止时间戳。
{dur_note}{_COMPLEX_SCENE_RULES}
任务2（动作初分析）：同步观察正反手击球、发球、截击、移动步伐与发力姿态。
{profile}

输出要求：仅输出 JSON，不要输出任何其他文字。JSON 结构：
{{
  "segments": [ {{"start": <秒>, "end": <秒>, "label": "ace|rally|winner|smash|other", "confidence": 0.0-1.0 }},
  "scene_type": "training|match|practice|unknown",
  "reasoning": "<一句话说明筛选逻辑>"
}}
"""


def build_report_prompt(level: str = "intermediate", highlight: HighlightResult | None = None) -> str:
    """技术分析报告 Prompt（基于高光结果深挖）。"""
    profile = _LEVEL_PROFILES.get(level, _LEVEL_PROFILES["intermediate"])
    seg_note = ""
    if highlight and highlight.segments:
        seg_note = (
            "已识别高光回合："
            + "; ".join(f"[{s.start:.1f}-{s.end:.1f}] {s.label}" for s in highlight.segments)
            + "。请围绕这些回合深入分析。"
        )
    return f"""你是网球技术教练 Agent。{profile}
{seg_note}
请基于视频与高光回合，输出结构化技术分析报告。重点覆盖：动作问题诊断、错误成因、针对性训练改进方案。

输出要求：仅输出 JSON，不要输出任何其他文字。JSON 结构：
{{
  "summary": "<2-3 句总体评价>",
  "strokes": [ {{"action": "forehand|backhand|serve|volley|footwork|grip|posture", "problem": "...", "cause": "...", "suggestion": "...", "severity": "low|moderate|high"}} ],
  "strengths": ["..."],
  "weaknesses": ["..."],
  "training_plan": ["..."]
}}
"""
