"""启动环境自检单元测试。

覆盖 FFMPEG / 数据库 / 模型提供商 / 数据目录四项检查的 ok/warn/fail 分支，
以及 run_startup_checks 聚合结构与 Mock 模式降级判定。
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from app.config import AppConfig, LLMConfig, ProviderConfig
from app.utils.environment import CheckResult, run_startup_checks


# ---------- CheckResult ----------

def test_check_result_to_dict():
    r = CheckResult("ffmpeg", "ok", "fine")
    assert r.to_dict() == {"name": "ffmpeg", "status": "ok", "detail": "fine"}


# ---------- FFMPEG ----------

def test_check_ffmpeg_ok(monkeypatch):
    from app.utils.environment import check_ffmpeg

    monkeypatch.setattr("app.utils.ffmpeg.is_available", lambda: True)
    assert check_ffmpeg().status == "ok"


def test_check_ffmpeg_fail(monkeypatch):
    from app.utils.environment import check_ffmpeg

    monkeypatch.setattr("app.utils.ffmpeg.is_available", lambda: False)
    result = check_ffmpeg()
    assert result.status == "fail"
    assert "ffmpeg" in result.detail


def test_check_ffmpeg_exc(monkeypatch):
    from app.utils.environment import check_ffmpeg

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr("app.utils.ffmpeg.is_available", boom)
    assert check_ffmpeg().status == "fail"


# ---------- 数据库 ----------

class _FakeConn:
    def execute(self, *args, **kwargs):  # noqa: D401, ANN001
        return None


class _FakeEngine:
    @contextmanager
    def connect(self):
        yield _FakeConn()


def test_check_database_ok(monkeypatch):
    from app.utils.environment import check_database

    monkeypatch.setattr("app.db.get_engine", lambda config: _FakeEngine())
    cfg = AppConfig()
    cfg.database.url = "sqlite:///./data_test/tennisclip.db"
    assert check_database(cfg).status == "ok"


def test_check_database_fail(monkeypatch):
    from app.utils.environment import check_database

    def boom(config):
        raise RuntimeError("cannot connect")

    monkeypatch.setattr("app.db.get_engine", boom)
    cfg = AppConfig()
    cfg.database.url = "postgresql+psycopg://u:p@127.0.0.1/db"
    result = check_database(cfg)
    assert result.status == "fail"
    assert "连接失败" in result.detail


# ---------- 模型提供商 ----------

def _provider_config(api_key_env: str = "FOO", mock_mode: str = "auto") -> AppConfig:
    cfg = AppConfig()
    cfg.llm = LLMConfig(
        providers={"default": ProviderConfig(
            name="default", base_url="http://x", api_key_env=api_key_env, model="m",
        )},
        active_provider="default",
        mock_mode=mock_mode,
    )
    return cfg


def test_check_provider_ok_with_key(monkeypatch):
    # 未初始化 DB 时 _resolve_effective_provider 回落静态 config（ai.provider=default）
    from app.services import db_service

    monkeypatch.setattr(db_service, "_SessionLocal", None)  # 强制回落静态 config，隔离真实 DB
    from app.utils.environment import check_provider

    cfg = _provider_config()
    monkeypatch.setenv("FOO", "secret")
    assert check_provider(cfg).status == "ok"


def test_check_provider_warn_mock_auto(monkeypatch):
    from app.utils.environment import check_provider

    cfg = _provider_config()
    monkeypatch.delenv("FOO", raising=False)
    result = check_provider(cfg)
    assert result.status == "warn"
    assert "Mock" in result.detail


def test_check_provider_fail_mock_never(monkeypatch):
    from app.utils.environment import check_provider

    cfg = _provider_config(mock_mode="never")
    monkeypatch.delenv("FOO", raising=False)
    assert check_provider(cfg).status == "fail"


def test_check_provider_fail_missing_field(monkeypatch):
    from app.services import db_service

    monkeypatch.setattr(db_service, "_SessionLocal", None)
    from app.utils.environment import check_provider

    cfg = AppConfig()
    cfg.llm = LLMConfig(
        providers={"default": ProviderConfig(
            name="default", base_url="", api_key_env="FOO", model="",
        )},
        active_provider="default",
        mock_mode="auto",
    )
    monkeypatch.delenv("FOO", raising=False)
    result = check_provider(cfg)
    assert result.status == "fail"
    assert "缺失字段" in result.detail


def test_check_provider_fail_unresolved(monkeypatch):
    from app.services import db_service

    monkeypatch.setattr(db_service, "_SessionLocal", None)
    from app.utils.environment import check_provider

    cfg = AppConfig()  # providers 为空，active_provider 无法解析
    assert check_provider(cfg).status == "fail"


# ---------- 数据目录 ----------

def test_check_data_dir_ok(tmp_path):
    from app.utils.environment import check_data_dir

    cfg = AppConfig()
    cfg.root = tmp_path
    cfg.paths.data_dir = "data"
    result = check_data_dir(cfg)
    assert result.status == "ok"
    assert (tmp_path / "data").is_dir()


def test_check_data_dir_fail(monkeypatch, tmp_path):
    from app.utils.environment import check_data_dir

    cfg = AppConfig()
    cfg.root = tmp_path
    cfg.paths.data_dir = "data"

    def boom():
        raise RuntimeError("read-only")

    monkeypatch.setattr(cfg, "ensure_data_dir", boom)
    assert check_data_dir(cfg).status == "fail"


# ---------- 聚合 ----------

def test_run_startup_checks_structure(monkeypatch, tmp_path):
    monkeypatch.setattr("app.utils.ffmpeg.is_available", lambda: True)
    monkeypatch.setattr("app.db.get_engine", lambda config: _FakeEngine())

    cfg = _provider_config()
    monkeypatch.setenv("FOO", "secret")
    cfg.root = tmp_path
    cfg.paths.data_dir = "data"

    checks = run_startup_checks(cfg)
    assert set(checks.keys()) == {"ffmpeg", "database", "provider", "data_dir"}
    for result in checks.values():
        assert isinstance(result, CheckResult)
        assert result.status in {"ok", "warn", "fail"}


def _seed_engine(tmp_path, monkeypatch, cfg):
    """建表并把 db_service 引擎指向测试库，写入 ai_providers + system_config。"""
    import app.db_models  # noqa: F401  确保 ORM 注册
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db_models import AiProvider, SystemConfig, _seed_system_config as db_models_seed_system_config
    from app.services import db_service

    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{tmp_path / 'data' / 'tennisclip.db'}"
    engine = create_engine(url)
    app.db_models.Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    with SessionLocal() as s:
        if s.query(AiProvider).count() == 0:
            s.add_all(db_service._seed_providers(cfg))
            s.commit()
        db_models_seed_system_config(cfg, s)
        s.commit()
    monkeypatch.setattr(db_service, "_engine", engine)
    monkeypatch.setattr(db_service, "_SessionLocal", SessionLocal)
    return engine


def test_resolve_effective_provider_from_db(tmp_path, monkeypatch):
    """_resolve_effective_provider 优先返回 ai.provider 引用的服务商记录（含 api_key）。"""
    from app.db_models import _seed_system_config as db_models_seed_system_config
    from app.utils.environment import _resolve_effective_provider

    cfg = AppConfig()
    cfg.llm = LLMConfig(
        providers={
            "default": ProviderConfig(name="default", base_url="http://x", api_key_env="FOO", model="m"),
            "openai": ProviderConfig(name="openai", base_url="http://o", api_key_env="BAR", model="g"),
        },
        active_provider="default",
    )
    # 默认种子把 ai.provider 设为 active_provider='default'
    engine = _seed_engine(tmp_path, monkeypatch, cfg)

    eff = _resolve_effective_provider(cfg)
    assert eff.name == "default"
    assert eff.api_key == ""  # 种子密钥为空
    assert eff.model == "m"
    assert eff.source == "database"

    # 切换 ai.provider 到 openai
    from app.db_models import SystemConfig
    from app.services import db_service

    with db_service.session() as s:
        row = s.query(SystemConfig).filter_by(key="ai.provider").first()
        row.value = "openai"
        s.commit()
    eff2 = _resolve_effective_provider(cfg)
    assert eff2.name == "openai"
    assert eff2.base_url == "http://o"
    assert eff2.source == "database"


def test_resolve_effective_provider_db_unavailable_falls_back(monkeypatch):
    """DB 不可用时回落静态 config（source=config）。"""
    from app.utils.environment import _resolve_effective_provider

    monkeypatch.setattr("app.services.db_service.session", None)  # 触发 DB 查询失败
    cfg = AppConfig()
    cfg.llm = LLMConfig(
        providers={"default": ProviderConfig(name="default", base_url="http://x", api_key_env="FOO", model="m")},
        active_provider="default",
    )
    eff = _resolve_effective_provider(cfg)
    assert eff.name == "custom"
    assert eff.base_url == "http://x"
    assert eff.source == "config"
