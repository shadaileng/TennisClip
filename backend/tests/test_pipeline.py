"""基础测试：配置加载、模型定义、Mock 流水线。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import load_config
from app.models import HighlightResult, Segment, TaskResult, TaskStatus, TechnicalReport


def test_load_config_defaults():
    config = load_config()
    provider = config.active_provider
    assert provider.name == "default"
    assert provider.base_url == "https://api.stepfun.com/v1"
    assert provider.api_key_env == "STEPFLASH_API_KEY"
    assert provider.model == "step3.7-flash"
    assert config.highlight.target_duration == 15
    assert config.queue.max_concurrent_tasks >= 1
    # providers 模式下应包含多个提供商
    assert "openai" in config.llm.providers
    assert "ollama" in config.llm.providers
    assert "vllm" in config.llm.providers


def test_segment_validation():
    seg = Segment(start=1.0, end=5.0, label="ace", confidence=0.9)
    assert seg.end > seg.start
    with pytest.raises(ValueError):
        Segment(start=1.0, end=5.0, label="ace", confidence=1.5)


def test_highlight_result_roundtrip():
    hl = HighlightResult(
        segments=[Segment(start=1.0, end=4.0, label="rally", confidence=0.9)],
        target_duration=15,
        scene_type="training",
    )
    data = hl.model_dump()
    restored = HighlightResult(**data)
    assert len(restored.segments) == 1


def test_task_result_default():
    result = TaskResult(task_id="t1")
    assert result.status == TaskStatus.PENDING


def test_fallback_report_shape():
    from app.services.report import _fallback_report

    hl = HighlightResult(segments=[Segment(start=0, end=5, label="ace", confidence=0.9)])
    report = _fallback_report(hl, level="beginner")
    assert report.level == "beginner"
    assert report.strokes
    assert report.training_plan


def test_mock_llm_structured():
    from app.utils import llm

    config = load_config()
    config.llm.mock_mode = "always"
    data = llm._mock_structured("HighlightResult", config, Path("dummy.mp4"))
    assert data is not None
    assert "segments" in data


def test_prompts_render():
    from prompts.highlight_analysis import build_highlight_prompt, build_report_prompt

    p1 = build_highlight_prompt(level="professional", duration_seconds=120.0)
    assert "JSON" in p1
    p2 = build_report_prompt(level="beginner")
    assert "summary" in p2


def test_task_queue_states():
    import time

    from app.utils.tasks import TaskQueue

    config = load_config()
    queue = TaskQueue(config)

    def job(result) -> TaskResult:
        time.sleep(0.05)
        return TaskResult(task_id="q1", status=TaskStatus.SUCCEEDED)

    tid = queue.submit(job)
    while queue.get(tid).status in ("pending", "processing"):
        time.sleep(0.01)
    assert queue.get(tid).status == TaskStatus.SUCCEEDED
    queue.shutdown()


def _stub_llm(monkeypatch, model, mock):
    """隔离网络/DB：stub 模型解析与 LLM 调用，仅验证 generated_by 文案逻辑。"""
    from app.services import report as report_svc

    monkeypatch.setattr(report_svc.llm, "complete_structured", lambda *a, **k: {
        "summary": "stub", "strokes": [], "strengths": [],
        "weaknesses": [], "training_plan": [],
    })
    monkeypatch.setattr(report_svc.llm, "resolve_effective", lambda c: (model, mock))


def test_report_generated_by_reflects_model(tmp_path, monkeypatch):
    """报告 generated_by 应反映界面真实选中模型，并在 mock 模式追加 (mock)；

    且先于落盘赋值，确保磁盘 *_report.json 内容正确（修复先落盘后赋值的历史 bug）。
    """
    import json

    from app.models import HighlightResult
    from app.services import report as report_svc

    _stub_llm(monkeypatch, "agnes-3.0-flash", True)

    hl = HighlightResult(segments=[], scene_type="training")
    out = tmp_path / "r_report.json"
    rep = report_svc.generate_report(
        video_path=tmp_path / "x.mp4",
        highlight=hl,
        config=load_config(),
        level="intermediate",
        out_path=out,
    )
    assert rep.generated_by == "TennisClip AI / agnes-3.0-flash (mock) (level=intermediate)"
    on_disk = json.loads(out.read_text(encoding="utf-8"))
    assert on_disk["generated_by"] == rep.generated_by


def test_report_generated_by_real_no_mock(tmp_path, monkeypatch):
    """真实调用（配了 API Key）时不应追加 (mock) 标注（回归：api_key 误判）。"""
    import json

    from app.models import HighlightResult
    from app.services import report as report_svc

    _stub_llm(monkeypatch, "agnes-2.5-flash", False)

    hl = HighlightResult(segments=[], scene_type="training")
    out = tmp_path / "r2_report.json"
    rep = report_svc.generate_report(
        video_path=tmp_path / "x.mp4",
        highlight=hl,
        config=load_config(),
        level="intermediate",
        out_path=out,
    )
    assert rep.generated_by == "TennisClip AI / agnes-2.5-flash (level=intermediate)"
    assert "(mock)" not in rep.generated_by


def test_find_highlights_training_uses_uniform_slices(tmp_path, monkeypatch):
    """练习/训练类视频应改用均匀切片覆盖全程，而非仅裁开头（修复整段判为 other）。"""
    from app.services import highlight as highlight_svc

    # stub LLM：模拟模型把整段 120s 判为单个 other（典型训练场景）
    monkeypatch.setattr(
        highlight_svc.llm, "complete_structured",
        lambda *a, **k: {
            "segments": [{"start": 0.0, "end": 120.0, "label": "other", "confidence": 0.95}],
            "scene_type": "training",
            "reasoning": "静态练习",
        },
    )

    hl = highlight_svc.find_highlights(
        tmp_path / "x.mp4", load_config(), duration_seconds=120.0, level="intermediate",
    )
    # 应切成 max_segments(3) 段、均匀散布，而非单段
    assert hl.scene_type == "training"
    assert len(hl.segments) == 3
    # 片段应覆盖全程（散布于 17.5/57.5/97.5 附近），而非都挤在开头
    assert hl.segments[0].start > 10          # 证明不是“从 0 裁前 15s”
    assert hl.segments[-1].end > 100          # 覆盖到视频后段
    assert hl.segments[-1].start - hl.segments[0].start > 50  # 整体跨度大
    for seg in hl.segments:
        assert (seg.end - seg.start) >= 3


def test_find_highlights_match_keeps_model_segments(tmp_path, monkeypatch):
    """比赛类视频保留模型高光筛选结果，不强制均匀切片。"""
    from app.services import highlight as highlight_svc

    monkeypatch.setattr(
        highlight_svc.llm, "complete_structured",
        lambda *a, **k: {
            "segments": [
                {"start": 3.0, "end": 7.5, "label": "ace", "confidence": 0.95},
                {"start": 40.0, "end": 45.0, "label": "winner", "confidence": 0.9},
            ],
            "scene_type": "match",
            "reasoning": "比赛",
        },
    )
    hl = highlight_svc.find_highlights(
        tmp_path / "x.mp4", load_config(), duration_seconds=120.0, level="intermediate",
    )
    labels = {s.label for s in hl.segments}
    assert labels == {"ace", "winner"}


def test_find_highlights_degenerate_candidate_falls_back_to_uniform(tmp_path, monkeypatch):
    """退化候选（单窗口覆盖整段，如单人练球全程连续运动）应回退均匀切片而非仅裁开头。"""
    from app.services import highlight as highlight_svc
    from app.models import Segment

    # 信号把整段当成一个候选窗口（运动铺满全程）——无定位价值
    monkeypatch.setattr(
        highlight_svc.event_detect, "detect_candidates",
        lambda *a, **k: [Segment(start=0.0, end=120.0, label="candidate", confidence=0.9)],
    )
    # 模型把整段判为单个 rally（典型训练场景）
    monkeypatch.setattr(
        highlight_svc.llm, "complete_structured",
        lambda *a, **k: {
            "segments": [{"start": 1.0, "end": 120.0, "label": "rally", "confidence": 0.5}],
            "scene_type": "practice",
            "reasoning": "全程练习",
        },
    )

    hl = highlight_svc.find_highlights(
        tmp_path / "x.mp4", load_config(), duration_seconds=120.0, level="intermediate",
    )
    assert hl.scene_type == "practice"
    assert len(hl.segments) == 3
    assert hl.segments[0].start > 10        # 不是从 0 裁前 15s
    assert hl.segments[-1].end > 100        # 覆盖到视频后段
    assert hl.segments[-1].start - hl.segments[0].start > 50


def test_complete_structured_uses_configured_timeout_and_retries(monkeypatch):
    """真实调用分支应按配置透传 timeout_seconds 与 max_retries 给 OpenAI 客户端。"""
    from app.utils import llm as llm_mod
    from types import SimpleNamespace

    captured = {}

    class _FakeCompletions:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(
                    content='{"segments":[],"scene_type":"unknown","reasoning":"x"}'))]
            )

    class _FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.chat = SimpleNamespace(completions=_FakeCompletions())

    monkeypatch.setattr(llm_mod, "OpenAI", _FakeClient)
    cfg = load_config()
    cfg.llm.mock_mode = "never"          # 强制走真实调用分支
    cfg.llm.timeout_seconds = 123
    cfg.llm.max_retries = 2
    # 视频不存在时抽帧降级为空列表（纯文本），不触发真实 ffmpeg 产物
    llm_mod.complete_structured(
        Path("/nonexistent_x.mp4"), "ignore", cfg, schema_hint="HighlightResult",
    )
    assert captured["timeout"] == 123
    assert captured["max_retries"] == 2


def test_database_config_loaded():
    """数据库配置加载：默认 SQLite，url 字段存在。"""
    config = load_config()
    assert config.database.url.startswith("sqlite")
    assert hasattr(config.database, "echo")


def test_db_models_import():
    """ORM 表结构可导入（SQLAlchemy 多兼容层）。"""
    from app import db_models

    assert db_models.Base is not None
    # 六张表（含 ai_providers 与 system_config 配置表）
    tables = set(db_models.Base.metadata.tables.keys())
    expected = {"tasks", "task_inputs", "task_outputs", "task_results", "ai_providers", "system_config", "files"}
    assert expected.issubset(tables), f"missing tables: {expected - tables}"


def test_db_service_roundtrip():
    """DB 服务层完整读写：任务/输入/输出/结果快照/提供商切换。"""
    from app.services import db_service
    from app.config import load_config
    from app.db_models import Task, TaskInput, TaskOutput, TaskResult as TRModel, FileRecord

    # 测试库落入 data_test/（测试环境统一数据目录），测试后清理，不触碰真实 data/
    cfg = load_config()
    data_dir = cfg.data_path
    data_dir.mkdir(parents=True, exist_ok=True)
    test_db = data_dir / "test_roundtrip.db"
    if test_db.exists():
        test_db.unlink()

    cfg.database.url = f"sqlite:///{test_db}"

    # 重新初始化引擎（指向临时库）
    import importlib
    importlib.reload(db_service)
    db_service.init_db(cfg)

    task_id = "roundtrip01"
    db_service.record_task_start(task_id, "in.mp4", "intermediate")
    db_service.record_task_input(
        task_id, data_dir / "in.mp4", 60.0, 1280, 720, 30.0, "intermediate"
    )
    db_service.record_task_output(task_id, "report", str(test_db), 0.01)
    db_service.record_task_finish(
        task_id, "succeeded", 12.3, None,
        highlight_json={"segments": []},
        report_json={"summary": "ok"},
        generated_by="test",
    )

    with db_service.session() as s:
        t = s.query(Task).filter_by(task_id=task_id).first()
        assert t is not None and t.status == "succeeded"
        assert t.elapsed_seconds == 12.3
        assert s.query(TaskInput).filter_by(task_id=task_id).count() == 1
        assert s.query(TaskOutput).filter_by(task_id=task_id).count() == 1
        r = s.query(TRModel).filter_by(task_id=task_id).first()
        assert r is not None and r.report_json is not None

    # 提供商种子 + 配置直选（ai.provider 覆盖）
    providers = db_service.list_providers()
    assert any(p["name"] == "default" for p in providers)
    default = next(p for p in providers if p["name"] == "default")
    assert default["is_selected"] is True  # 种子默认选中 active_provider

    from app.services import config_service

    with db_service.session() as s:
        config_service.set_config_value(s, "ai.provider", "openai")
    selected = [p for p in db_service.list_providers() if p["is_selected"]]
    assert len(selected) == 1 and selected[0]["name"] == "openai"

    # 清理（关闭引擎释放锁后再删；Windows 下若仍被占用则跳过删除不影响断言）
    try:
        import gc
        gc.collect()
        if test_db.exists():
            test_db.unlink()
    except OSError:
        pass  # 文件仍被 SQLite 句柄占用，留待下次运行前清理


