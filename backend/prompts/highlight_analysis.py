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


_PREP_EXCLUSION = """
准备段排除规则（非常重要）：
- 仅选择真实击球/相持时刻及其前后挥拍动作：ACE、发球、正手/反手抽击、截击、高压扣杀、多拍相持。
- 以"一个完整回合"为单元返回，保留每分从准备到随挥结束的完整性，不要切碎连续对拉。
- 严禁将以下"回合之间的死时间"选为高光，必须剔除：捡球、弯腰拾球、走位移动、等待、调整站位/握拍、喝水休息、与教练说话；以及不属于任何连续对拉的孤立失误（打丢、下网、出界、空挥）。
- 候选窗口已通过音频击球瞬态（球拍触球声）与运动强度联合锚定在真实动作上，请在其附近 ±2s 精修起止。
- 若某候选窗口主要是准备/过渡内容而非真实击球，请不要返回它（不要使用 other 填充）。
"""

_PREP_EXCLUSION_VIDEO = """
准备段排除规则（非常重要）：
- 把视频按"连续击球 — 停顿 — 再击球"的节奏切成若干段：每一段是一次连续的击球 / 相持片段，
  段与段之间是被捡球、走位、等待、休息、与教练说话等"死时间"隔开的。
- 每个回合的【起点】应是该次击球的"准备动作"：分腿垫步、侧身、引拍、准备站位 / 等待来球等击球前的准备姿态，
  而不是触球瞬间——让剪辑片段从准备开始，自然过渡到击球与随挥结束。一次连续击球片段（准备 → 击球 → 随挥）视为一个完整回合，请保持完整，
  不要把它切成许多 2-3 秒的碎片。
- 注意区分两类"准备"：紧贴某次击球前的"准备站位 / 引拍 / 分腿垫步"属于回合、应保留在该段起点；
  与击球无关、发生在回合之间的"走位调整 / 重新找位 / 等球发呆"才是死时间、应剔除。务必剔除的段间死时间是：
  捡球、弯腰拾球、走位移动、等待、与击球无关的调整站位 / 握拍、喝水休息、与教练说话，
  以及不属于任何连续击球的孤立失误 / 空挥。把这些间隙从集锦中去掉。
- 严禁返回"覆盖整段视频的单一长段"（那等于没有剪辑）；也严禁把一次连续击球碎成几十个超短段。
- 属于某个连续击球片段里的结束拍（制胜分或下网 / 出界）保留在该段内，不要因"是失误"就切断。
- 请直接观看整段视频，独立判断每个击球片段的起止时间戳，不限定于候选窗口。
- 若整段主要是准备 / 过渡内容而非任何真实击球，请不要返回它（不要使用 other 填充）。
"""


def build_highlight_prompt(
    level: str = "intermediate",
    duration_seconds: float | None = None,
    candidates: list | None = None,
    select_all: bool = False,
    mode: str = "frame",
) -> str:
    """高光识别 + 双任务并行 Prompt。

    candidates: 由 ffmpeg 信号定位的候选窗口（带真实时间戳），用于把 LLM 的"何时"
    约束在信号锚定范围内，避免时间戳臆造。
    select_all: 所有高光回合模式——指示模型每个候选窗口都是真实动作回合、全部返回，
    不做数量取舍（与 level 策略的 all 档位对应）。
    mode: 媒体输入策略（frame=抽帧 / video=视频理解）。video 模式弱化 ±2s 精修、
    改为"直接观看整段视频独立定位"，候选仅作参考提示。
    """
    profile = _LEVEL_PROFILES.get(level, _LEVEL_PROFILES["intermediate"])
    dur_note = f"\n视频总时长约 {duration_seconds:.1f} 秒。" if duration_seconds else ""
    cand_note = ""
    if candidates:
        cand_lines = "\n".join(
            f"- 候选窗口 {i + 1}：[{c.start:.1f}s, {c.end:.1f}s]"
            for i, c in enumerate(candidates)
        )
        if mode == "video":
            cand_note = (
                "\n以下为信号定位的参考候选高光窗口（仅供参考，请直接观看整段视频独立判断，"
                "可在任意位置定位高光，不必拘泥于窗口）：\n"
                f"{cand_lines}\n"
            )
        elif select_all:
            cand_note = (
                "\n已通过视频信号（音频击球瞬态 + 运动强度）定位以下候选高光窗口，时间戳为真实秒数：\n"
                f"{cand_lines}\n"
                "【所有高光回合模式】每一个候选窗口都是一次真实挥拍/相持回合，请逐一判断并在各自 ±2s 内"
                "精修起止时间戳，**全部返回、不得遗漏、不得取舍数量**；与候选窗口无关的片段请勿返回。"
            )
        else:
            cand_note = (
                "\n已通过视频信号（音频击球瞬态 + 运动强度）定位以下候选高光窗口，时间戳为真实秒数：\n"
                f"{cand_lines}\n"
                "请仅在这些窗口内判断是否为高光（ace/rally/winner/smash/forehand/backhand/volley/serve），"
                "并在对应窗口 ±2s 内精修起止时间戳；与候选窗口无关的片段请勿返回。"
            )
    prep = _PREP_EXCLUSION_VIDEO if mode == "video" else _PREP_EXCLUSION
    return f"""你是网球视频分析 Agent。请观看视频，完成以下任务：

任务1（高光识别）：筛选真实击球与相持回合（ACE、发球、正手/反手抽击、截击、高压扣杀、多拍相持），输出起止时间戳。
把视频按"连续击球—死时间—连续击球"的节奏切成若干段：每段是一次连续击球 / 相持片段（保持内部完整、含约 1s 前后缓冲），段间的死时间（捡球 / 走位 / 等待 / 休息 / 孤立失误）剔除不返回。不要返回覆盖整段的单一长段，也不要切碎连续击球。
{dur_note}{cand_note}{prep}{_COMPLEX_SCENE_RULES}
任务2（动作初分析）：同步观察正反手击球、发球、截击、移动步伐与发力姿态。
{profile}

输出要求：仅输出 JSON，不要输出任何其他文字。JSON 结构（scene_type 与 reasoning 必须是顶层字段，不要写进每个 segment 内）：
{{
  "segments": [ {{"start": <秒>, "end": <秒>, "label": "ace|rally|winner|smash|forehand|backhand|volley|serve|out|error", "confidence": 0.0-1.0 }} ],
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
