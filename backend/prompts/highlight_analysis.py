"""领域 Prompt 模板：网球教学知识库注入点。

设计原则（需求文档 2.3）：
- 固定执行逻辑 + 强制 JSON 结构化输出；
- 按用户层级（beginner/intermediate/professional）差异化分析口径；
- 复杂场景（高速击球/遮挡/暗光）判别规则显式写入 Prompt（风险1 规避）；
- 视觉判别规则 + few-shot 示例 + prompt_variant 变体（方案 12 · 阶段 1 Step 1.3）；
- 报告注入分层教学知识库与网球领域知识（Step 1.1/1.2/1.5）。
"""

from __future__ import annotations

from app.models import HighlightResult
from prompts.report_templates import knowledge_for
from prompts.tennis_domain import domain_knowledge, label_reference

_COMPLEX_SCENE_RULES = """
复杂场景判别规则：
- 高速击球（球速 > 120km/h）：以击球瞬间身体姿态为准，忽略球体模糊；
- 遮挡画面：结合前后 0.5s 帧的球路轨迹插值判断；
- 暗光画面：降低该片段置信度上限至 0.8，并在 reasoning 中说明。
"""

_VISUAL_DISCRIMINATION_RULES = """
视觉判别规则（按画面特征识别，勿依赖音频）：
- 发球 vs 正手：发球 = 底线后抛球 + 过顶击球 + 身体向上蹬伸；正手 = 侧身引拍 + 转体挥拍，击球点在腰部高度；
- 双手反拍 vs 单手反拍：双手反拍引拍时非持拍手扶拍颈随转，击球瞬间双手不分离；单手反拍引拍时拍头竖起、随挥向斜上方延展；
- 截击 vs 落地球：截击发生在网前且球未落地（无完整引拍、动作短促）；落地球均有引拍-挥拍完整弧线；
- 高压扣杀 vs 发球：高压 = 侧身后退 + 向前上方扣压（来球来自对方挑高）；发球 = 静止抛球起手；
- 回合 vs 死时间：只要画面中"球在双方场区上空连续往返"即为回合（含落地弹起）；捡球、走位、擦汗、整理线床、与教练交谈为死时间；
- 制胜分 vs 对手失误：制胜分 = 击球后对手未触及（或触及未过网）；对手失误 = 对手击球下网/出界，两者都可入高光但 reasoning 中注明成因；
- 标签归属以"主导动作"为准：发球+接发构成的短回合标 serve/ace（以发球质量定），相持 4 拍以上标 rally。
"""

_FEW_SHOT_EXAMPLES = """
few-shot 示例（仅示范判断口径，时间戳须按实际视频重新推断）：
- 例1（ACE）：画面为一方底线抛球过顶击球，球飞向对方发球区，接发方挥拍未触及 → label=ace，confidence 0.9+，段起点取抛球前 0.5s，终点取接发动作结束；
- 例2（多拍相持）：连续 5 拍对拉后一方制胜 → 整段标 rally（勿拆成单拍），起点取该分准备姿态（分腿垫步/引拍），终点取最后一拍拍头随挥结束；
- 例3（死时间剔除）：球员弯腰捡球 → 走回底线 → 整理拍线 → 等待发球 → 发球开始：仅"发球开始"之后入段，前三段死时间一律剔除；
- 例4（训练场景）：教练喂球 + 学员连续正手击球，无对手回球 → 若整段无对抗回合，scene_type=training 且各段仅保留挥拍完整、动作清晰的击球片段。
"""

# prompt_variant 变体（Step 1.4）：standard=默认；strict=宁缺毋滥；teaching=保留教学素材
_PROMPT_VARIANT_RULES = {
    "standard": "",
    "strict": """
严格模式（strict）补充约束：
- confidence < 0.6 的候选一律不返回；宁可少返回，也不返回把握不足的段；
- 每段必须包含至少一次完整挥拍/击球；仅"看起来像动作"的模糊片段剔除；
- 段边界收紧：起点不得早于准备动作开始，终点不得晚于随挥结束（±0.5s 内）；
- reasoning 中须给出该段的视觉依据（如"抛球+蹬伸+过顶击球"）。
""",
    "teaching": """
教学模式（teaching）补充约束：
- 优先保留"技术动作完整可分析"的回合（含准备-引拍-击球-随挥全过程），供后续技术报告使用；
- 对典型错误动作（如引拍过大、击球点靠后）同样保留，它们是教学分析的关键素材；
- 每段尽量覆盖从准备姿态到随挥结束的完整链路，勿在动作中途截断；
- 段数上限放宽：可多保留 1-2 个动作清晰的回合，宁多勿漏。
""",
}

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
    prompt_variant: str = "standard",
) -> str:
    """高光识别 + 双任务并行 Prompt。

    candidates: 由 ffmpeg 信号定位的候选窗口（带真实时间戳），用于把 LLM 的"何时"
    约束在信号锚定范围内，避免时间戳臆造。
    select_all: 所有高光回合模式——指示模型每个候选窗口都是真实动作回合、全部返回，
    不做数量取舍（与 level 策略的 all 档位对应）。
    mode: 媒体输入策略（frame=抽帧 / video=视频理解）。video 模式弱化 ±2s 精修、
    改为"直接观看整段视频独立定位"，候选仅作参考提示。
    prompt_variant: Prompt 变体（standard/strict/teaching），注入差异化补充约束；
    非法值回落 standard（与 ParamSpec options 校验互补）。
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
    variant_rules = _PROMPT_VARIANT_RULES.get(prompt_variant, _PROMPT_VARIANT_RULES["standard"])
    return f"""你是网球视频分析 Agent。请观看视频，完成以下任务：

任务1（高光识别）：筛选真实击球与相持回合（ACE、发球、正手/反手抽击、截击、高压扣杀、多拍相持），输出起止时间戳。
把视频按"连续击球—死时间—连续击球"的节奏切成若干段：每段是一次连续击球 / 相持片段（保持内部完整、含约 1s 前后缓冲），段间的死时间（捡球 / 走位 / 等待 / 休息 / 孤立失误）剔除不返回。不要返回覆盖整段的单一长段，也不要切碎连续击球。
{dur_note}{cand_note}{prep}{_COMPLEX_SCENE_RULES}{_VISUAL_DISCRIMINATION_RULES}
{label_reference()}
{_FEW_SHOT_EXAMPLES}{variant_rules}
任务2（动作初分析）：同步观察正反手击球、发球、截击、移动步伐与发力姿态。
{profile}

输出要求：仅输出 JSON，不要输出任何其他文字。JSON 结构（scene_type 与 reasoning 必须是顶层字段，不要写进每个 segment 内）：
{{
  "segments": [ {{"start": <秒>, "end": <秒>, "label": "ace|rally|winner|smash|forehand|backhand|volley|serve|out|error", "confidence": 0.0-1.0 }} ],
  "scene_type": "training|match|practice|unknown",
  "reasoning": "<一句话说明筛选逻辑>"
}}
"""


def build_report_prompt(
    level: str = "intermediate",
    highlight: HighlightResult | None = None,
    knowledge_level: str = "standard",
) -> str:
    """技术分析报告 Prompt（基于高光结果深挖）。

    knowledge_level: 教学知识库深度档（basic/standard/expert），控制注入的
    教学要点条数（Step 1.1）与网球领域知识章节数（Step 1.2）；非法值回落 standard。
    """
    profile = _LEVEL_PROFILES.get(level, _LEVEL_PROFILES["intermediate"])
    seg_note = ""
    if highlight and highlight.segments:
        seg_note = (
            "已识别高光回合："
            + "; ".join(f"[{s.start:.1f}-{s.end:.1f}] {s.label}" for s in highlight.segments)
            + "。请围绕这些回合深入分析。"
        )
    knowledge_items = knowledge_for(level, knowledge_level)
    knowledge_note = "教学要点库（按 knowledge_level 档位注入，诊断与建议须优先引用）：\n" + "\n".join(
        f"- {item}" for item in knowledge_items
    )
    domain_note = "网球规则与生物力学知识：\n" + domain_knowledge(knowledge_level)
    return f"""你是网球技术教练 Agent。{profile}
{seg_note}
{knowledge_note}
{domain_note}
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
