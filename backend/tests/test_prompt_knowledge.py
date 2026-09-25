"""Step 1 测试：阶段 1 — 既有节点 Prompt 增强（方案 12 · 1.1~1.6）。

覆盖：
- report_templates 教学知识库分档（20+ 条/级，basic ⊂ standard ⊂ expert）；
- tennis_domain 领域知识模块渲染与档位缩放；
- highlight_analysis 视觉判别规则 / few-shot / prompt_variant 变体 / 报告知识注入；
- 节点 ParamSpec（prompt_variant / knowledge_level）resolve_params 校验 + schema 输出 + 透传。
"""

from __future__ import annotations

import pytest

from app.config import AppConfig
from app.models import HighlightResult, Segment, TechnicalReport
from app.workflow.spec import build_schema, get_spec, resolve_params

import app.workflow.nodes  # noqa: F401  确保内置节点注册

from tests.test_workflow_nodes import FakeContext


# ---------- 1.1 教学知识库分档 ----------

def test_teaching_knowledge_20plus_per_level():
    """每个层级教学要点 ≥ 20 条（3 档累积后达 20+ 条/级要求）。"""
    from prompts.report_templates import KNOWLEDGE_LEVELS, TEACHING_KNOWLEDGE, knowledge_for

    for level in ("beginner", "intermediate", "professional"):
        items = knowledge_for(level, depth="expert")
        assert len(items) >= 20, f"{level} 层级 expert 档仅 {len(items)} 条"
        # 三档结构完整
        assert set(TEACHING_KNOWLEDGE[level].keys()) == set(KNOWLEDGE_LEVELS)


def test_knowledge_for_depth_is_cumulative():
    """basic ⊂ standard ⊂ expert 累积包含关系；未知层级/档位回落。"""
    from prompts.report_templates import knowledge_for

    basic = set(knowledge_for("beginner", depth="basic"))
    standard = set(knowledge_for("beginner", depth="standard"))
    expert = set(knowledge_for("beginner", depth="expert"))

    assert basic < standard < expert  # 真子集（累积且逐档增多）
    # 未知层级回落 intermediate
    assert knowledge_for("no-such-level", depth="basic") == knowledge_for("intermediate", depth="basic")
    # 未知档位回落 standard
    assert knowledge_for("beginner", depth="no-such-depth") == knowledge_for("beginner", depth="standard")


# ---------- 1.2 网球领域知识模块 ----------

def test_tennis_domain_renders_and_scales():
    """domain_knowledge 按档位缩放（章节注入数递增），label_reference 含判分依据。"""
    from prompts.tennis_domain import BIOMECHANICS, RULES, domain_knowledge, label_reference

    basic = domain_knowledge("basic")
    standard = domain_knowledge("standard")
    expert = domain_knowledge("expert")

    # 章节标题随档位增多
    assert "【判分规则】" in basic and "【动力链】" in basic
    assert "【计分与胜负】" not in basic and "【计分与胜负】" in standard
    assert "【场地与器材】" in expert and "【移动与制动】" in expert
    # 篇幅单调递增
    assert len(basic) < len(standard) < len(expert)
    # 未知档位回落 standard
    assert domain_knowledge("no-depth") == standard

    # 标签判据引用完整
    ref = label_reference()
    assert "ace" in ref and "winner" in ref and "fault" in ref

    # 数据完整性：每章至少 3 条
    for group in (RULES, BIOMECHANICS):
        for items in group.values():
            assert len(items) >= 3


# ---------- 1.3 视觉判别规则 + few-shot ----------

def test_highlight_prompt_contains_visual_rules_and_fewshot():
    """build_highlight_prompt 注入视觉判别规则、标签判据与 few-shot 示例。"""
    from prompts.highlight_analysis import build_highlight_prompt

    p = build_highlight_prompt(level="intermediate", duration_seconds=60.0)
    assert "视觉判别规则" in p
    assert "双手反拍引拍时非持拍手" in p   # 具体判别条目
    assert "标签判据（网球规则依据）" in p  # tennis_domain.label_reference 注入
    assert "few-shot 示例" in p
    assert "例4（训练场景）" in p
    # 原有结构不回归
    assert "JSON" in p
    assert "准备段排除规则" in p


# ---------- 1.4 prompt_variant 变体 ----------

def test_prompt_variant_variants_differ():
    """standard=无附加约束；strict/teaching 注入各自补充约束；未知变体回落 standard。"""
    from prompts.highlight_analysis import build_highlight_prompt

    common = dict(level="intermediate", duration_seconds=60.0)
    p_standard = build_highlight_prompt(prompt_variant="standard", **common)
    p_strict = build_highlight_prompt(prompt_variant="strict", **common)
    p_teaching = build_highlight_prompt(prompt_variant="teaching", **common)
    p_fallback = build_highlight_prompt(prompt_variant="no-such", **common)

    assert "严格模式（strict）" not in p_standard
    assert "严格模式（strict）" in p_strict
    assert "confidence < 0.6" in p_strict
    assert "教学模式（teaching）" in p_teaching
    assert "技术动作完整可分析" in p_teaching
    # 未知变体回落 standard（不追加任何约束）
    assert p_fallback == p_standard


# ---------- 1.1 + 1.2 + 1.5 报告知识注入 ----------

def test_report_prompt_injects_knowledge_by_level():
    """build_report_prompt 按 knowledge_level 注入教学要点库与领域知识。"""
    from prompts.highlight_analysis import build_report_prompt

    hl = HighlightResult(segments=[Segment(start=1.0, end=5.0, label="ace", confidence=0.9)])

    p_basic = build_report_prompt(level="beginner", highlight=hl, knowledge_level="basic")
    p_expert = build_report_prompt(level="beginner", highlight=hl, knowledge_level="expert")
    p_default = build_report_prompt(level="beginner", highlight=hl)

    # 教学要点库注入
    assert "教学要点库" in p_basic
    assert "网球规则与生物力学知识" in p_basic
    # expert 比 basic 注入更多内容
    assert len(p_expert) > len(p_basic)
    assert "【场地与器材】" in p_expert and "【场地与器材】" not in p_basic
    # 默认档 = standard
    assert p_default == build_report_prompt(level="beginner", highlight=hl, knowledge_level="standard")
    # 层级 profile 与高光段仍注入（不回归）
    assert "面向入门学员" in p_basic
    assert "[1.0-5.0] ace" in p_basic
    assert "summary" in p_basic


# ---------- 1.4/1.5 节点 ParamSpec：resolve_params 校验 + schema 输出 ----------

def test_analyze_highlight_prompt_variant_param_spec():
    """analyze.highlight 含 prompt_variant ParamSpec：options/默认值/校验/schema。"""
    spec = get_spec("analyze.highlight")
    assert spec is not None
    pv = next((p for p in spec.params if p.key == "prompt_variant"), None)
    assert pv is not None
    assert pv.type == "select"
    assert pv.default == "standard"
    assert pv.options == ["standard", "strict", "teaching"]

    # resolve_params：缺省回填 + 合法值通过 + 非法值拒绝
    resolved = resolve_params(spec.params, {})
    assert resolved["prompt_variant"] == "standard"
    resolved = resolve_params(spec.params, {"prompt_variant": "strict"})
    assert resolved["prompt_variant"] == "strict"
    with pytest.raises(ValueError, match="prompt_variant"):
        resolve_params(spec.params, {"prompt_variant": "no-such"})

    # schema 输出携带该参数
    node = next(n for n in build_schema() if n["type"] == "analyze.highlight")
    keys = {p["key"]: p for p in node["params"]}
    assert "prompt_variant" in keys
    assert keys["prompt_variant"]["options"] == ["standard", "strict", "teaching"]


def test_report_technical_knowledge_level_param_spec():
    """report.technical 含 knowledge_level ParamSpec：options/默认值/校验/schema。"""
    spec = get_spec("report.technical")
    assert spec is not None
    kl = next((p for p in spec.params if p.key == "knowledge_level"), None)
    assert kl is not None
    assert kl.type == "select"
    assert kl.default == "standard"
    assert kl.options == ["basic", "standard", "expert"]

    resolved = resolve_params(spec.params, {})
    assert resolved["knowledge_level"] == "standard"
    with pytest.raises(ValueError, match="knowledge_level"):
        resolve_params(spec.params, {"knowledge_level": "no-such"})

    node = next(n for n in build_schema() if n["type"] == "report.technical")
    keys = {p["key"]: p for p in node["params"]}
    assert keys["knowledge_level"]["options"] == ["basic", "standard", "expert"]


# ---------- 1.4/1.5 节点透传 ----------

def test_analyze_highlight透传_prompt_variant(tmp_path, monkeypatch):
    """analyze.highlight 的 prompt_variant 透传至 highlight.find_highlights。"""
    from app.workflow.nodes import analyze_highlight

    captured = {}

    def fake_highlights(video, config, duration, level="intermediate", analysis_mode=None,
                        model=None, candidates=None, prompt_variant="standard", task_id=""):
        captured["prompt_variant"] = prompt_variant
        return HighlightResult(segments=[], scene_type="match")

    from app.services import highlight as hl_svc
    monkeypatch.setattr(hl_svc, "find_highlights", fake_highlights)

    ctx = FakeContext(config=AppConfig(),
                      inputs={"video": tmp_path / "v.mp4", "duration": 60.0})
    analyze_highlight.run(ctx, {"level": "intermediate", "analysis_mode": "frame",
                                "prompt_variant": "teaching"})
    assert captured["prompt_variant"] == "teaching"


def test_report_technical透传_knowledge_level(tmp_path, monkeypatch):
    """report.technical 的 knowledge_level 透传至 report.generate_report。"""
    from app.workflow.nodes import report_technical

    captured = {}

    def fake_report(video, highlight, config, level="intermediate", out_path=None,
                    knowledge_level="standard", task_id=""):
        captured["knowledge_level"] = knowledge_level
        return TechnicalReport(level=level, summary="stub")

    from app.services import report as report_svc
    monkeypatch.setattr(report_svc, "generate_report", fake_report)

    ctx = FakeContext(
        video_path=tmp_path / "v.mp4",
        task_out=tmp_path,
        inputs={"video": tmp_path / "v.mp4",
                "highlight": HighlightResult(segments=[], scene_type="match")},
    )
    report_technical.run(ctx, {"level": "intermediate", "knowledge_level": "expert"})
    assert captured["knowledge_level"] == "expert"
