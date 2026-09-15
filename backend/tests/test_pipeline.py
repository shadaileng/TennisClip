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

    def job() -> TaskResult:
        time.sleep(0.05)
        return TaskResult(task_id="q1", status=TaskStatus.SUCCEEDED)

    tid = queue.submit(job)
    while queue.get(tid).status in ("pending", "processing"):
        time.sleep(0.01)
    assert queue.get(tid).status == TaskStatus.SUCCEEDED
    queue.shutdown()


def test_database_config_loaded():
    """数据库配置加载：默认 SQLite，url 字段存在。"""
    config = load_config()
    assert config.database.url.startswith("sqlite")
    assert hasattr(config.database, "echo")


def test_db_models_import():
    """ORM 表结构可导入（SQLAlchemy 多兼容层）。"""
    from app import db_models

    assert db_models.Base is not None
    # 六张表
    tables = set(db_models.Base.metadata.tables.keys())
    expected = {"tasks", "task_inputs", "task_outputs", "task_results", "model_providers", "files"}
    assert expected.issubset(tables), f"missing tables: {expected - tables}"


def test_db_service_roundtrip():
    """DB 服务层完整读写：任务/输入/输出/结果快照/提供商切换。"""
    from app.services import db_service
    from app.config import load_config
    from app.db_models import Task, TaskInput, TaskOutput, TaskResult as TRModel, ModelProvider, FileRecord

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

    # 提供商种子 + 动态切换
    providers = db_service.list_providers()
    assert any(p.name == "default" for p in providers)
    assert db_service.activate_provider("openai")
    active = [p for p in db_service.list_providers() if p.is_active]
    assert len(active) == 1 and active[0].name == "openai"

    # 清理（关闭引擎释放锁后再删；Windows 下若仍被占用则跳过删除不影响断言）
    try:
        import gc
        gc.collect()
        if test_db.exists():
            test_db.unlink()
    except OSError:
        pass  # 文件仍被 SQLite 句柄占用，留待下次运行前清理


