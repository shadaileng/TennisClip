"""模型服务商 CRUD / 配置覆盖 / 掩码 单元测试（对齐 TennisDiary）。

模式（与 test_environment.py 一致）：
- 直接按 ORM Base.metadata 建表（create_all），不依赖 Alembic 迁移；
- 把 db_service._engine / _SessionLocal 指向测试引擎，隔离真实数据。

覆盖：
- 新增/唯一性/base_url/models 校验
- 列表排序（sort_order/name）+ is_selected 计算
- 编辑（api_key 留空保留、models/default_model 更新）
- 删除（被 ai.provider 引用项拒绝 409、按 id 成功、不存在 404）
- api_key 掩码（前3 + 末4，不泄漏明文）
- mask_secret 工具
- 配置 KV：get/set/delete 覆盖、ai.provider 引用解析（get_ai_config）
- 种子（config -> ai_providers + system_config.ai.provider）
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.db_models  # noqa: F401  确保 ORM 注册
from app.config import AppConfig, LLMConfig, ProviderConfig
from app.db_models import AiProvider, Base, SystemConfig, _seed_system_config
from app.services import config_service, db_service


def _make_engine(tmp_path):
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{tmp_path / 'data' / 'tennisclip.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    return engine


def _provider_db(tmp_path, monkeypatch):
    """建表并把 db_service 引擎指向测试库，返回 SessionLocal。"""
    engine = _make_engine(tmp_path)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(db_service, "_engine", engine)
    monkeypatch.setattr(db_service, "_SessionLocal", SessionLocal)
    return SessionLocal


def _set_config(SessionLocal, key: str, value: str) -> None:
    with SessionLocal() as s:
        row = s.query(SystemConfig).filter_by(key=key).first()
        if row is None:
            s.add(SystemConfig(key=key, value=value))
        else:
            row.value = value
        s.commit()


# ---------- 新增 ----------

def test_create_provider_basic(tmp_path, monkeypatch):
    _provider_db(tmp_path, monkeypatch)
    item = db_service.create_provider(
        name="openai",
        base_url="https://api.openai.com/v1",
        api_key="sk-1234567890abcdef",
        models=["gpt-4o", "gpt-4o-mini"],
    )
    assert item["name"] == "openai"
    assert item["models"] == ["gpt-4o", "gpt-4o-mini"]
    assert item["default_model"] == "gpt-4o"
    assert item["enabled"] is True
    assert item["is_selected"] is False
    # 掩码：前3 + 末4，明文不可见
    assert "sk-1234567890abcdef" not in item["api_key"]
    assert item["api_key"] == "sk-****cdef"
    assert item["api_key"].endswith("cdef")


def test_create_provider_empty_name(tmp_path, monkeypatch):
    _provider_db(tmp_path, monkeypatch)
    import pytest

    from app.services.db_service import ProviderNotFound

    with pytest.raises(ProviderNotFound):
        db_service.create_provider(name="  ", base_url="https://x/v1", api_key="k", models=["m"])


def test_create_provider_duplicate_name(tmp_path, monkeypatch):
    _provider_db(tmp_path, monkeypatch)
    import pytest

    from app.services.db_service import ProviderConflict

    db_service.create_provider(name="dup", base_url="https://x/v1", api_key="k", models=["m"])
    with pytest.raises(ProviderConflict):
        db_service.create_provider(name="dup", base_url="https://y/v1", api_key="k", models=["m2"])


def test_create_provider_invalid_base_url(tmp_path, monkeypatch):
    _provider_db(tmp_path, monkeypatch)
    import pytest

    with pytest.raises(ValueError):
        db_service.create_provider(name="bad", base_url="ftp://x", api_key="k", models=["m"])


def test_create_provider_empty_models(tmp_path, monkeypatch):
    _provider_db(tmp_path, monkeypatch)
    import pytest

    with pytest.raises(ValueError):
        db_service.create_provider(name="bad", base_url="https://x/v1", api_key="k", models=["", "  "])


def test_create_provider_trims_models(tmp_path, monkeypatch):
    _provider_db(tmp_path, monkeypatch)
    item = db_service.create_provider(
        name="trim", base_url="https://x/v1", api_key="k", models=[" m1 ", "", "m2"]
    )
    assert item["models"] == ["m1", "m2"]


# ---------- 列表 / 排序 / is_selected ----------

def test_list_providers_ordered(tmp_path, monkeypatch):
    _provider_db(tmp_path, monkeypatch)
    db_service.create_provider(name="b", base_url="https://x/v1", api_key="k", models=["m"], sort_order=2)
    db_service.create_provider(name="a", base_url="https://x/v1", api_key="k", models=["m"], sort_order=1)
    db_service.create_provider(name="c", base_url="https://x/v1", api_key="k", models=["m"], sort_order=1)
    names = [p["name"] for p in db_service.list_providers()]
    assert names == ["a", "c", "b"]


def test_list_providers_marks_is_selected(tmp_path, monkeypatch):
    SessionLocal = _provider_db(tmp_path, monkeypatch)
    db_service.create_provider(name="a", base_url="https://x/v1", api_key="k", models=["m"])
    db_service.create_provider(name="b", base_url="https://x/v1", api_key="k", models=["m"])
    _set_config(SessionLocal, "ai.provider", "b")
    listed = {p["name"]: p for p in db_service.list_providers()}
    assert listed["b"]["is_selected"] is True
    assert listed["a"]["is_selected"] is False


def test_list_providers_masks_api_key(tmp_path, monkeypatch):
    _provider_db(tmp_path, monkeypatch)
    db_service.create_provider(name="p", base_url="https://x/v1", api_key="sk-secret-plain", models=["m"])
    listed = db_service.list_providers()
    assert listed[0]["api_key"] != "sk-secret-plain"
    assert "sk-secret-plain" not in listed[0]["api_key"]


# ---------- 编辑 ----------

def test_update_provider_changes_models(tmp_path, monkeypatch):
    _provider_db(tmp_path, monkeypatch)
    created = db_service.create_provider(name="p", base_url="https://x/v1", api_key="k", models=["m1", "m2"])
    updated = db_service.update_provider(
        created["id"], name="p", base_url="https://x/v1", api_key="", models=["m3", "m4"]
    )
    assert updated["models"] == ["m3", "m4"]
    assert updated["default_model"] == "m3"


def test_update_provider_keeps_api_key_when_blank(tmp_path, monkeypatch):
    _provider_db(tmp_path, monkeypatch)
    created = db_service.create_provider(name="p", base_url="https://x/v1", api_key="sk-original", models=["m"])
    updated = db_service.update_provider(
        created["id"], name="p", base_url="https://x/v1", api_key="", models=["m"]
    )
    assert updated["api_key"] == "sk-****inal"
    assert updated["api_key"].endswith("inal")


def test_update_provider_rename_duplicate(tmp_path, monkeypatch):
    _provider_db(tmp_path, monkeypatch)
    import pytest

    from app.services.db_service import ProviderConflict

    db_service.create_provider(name="a", base_url="https://x/v1", api_key="k", models=["m"])
    b = db_service.create_provider(name="b", base_url="https://x/v1", api_key="k", models=["m"])
    with pytest.raises(ProviderConflict):
        db_service.update_provider(b["id"], name="a", base_url="https://x/v1", api_key="k", models=["m"])


def test_update_provider_not_found(tmp_path, monkeypatch):
    _provider_db(tmp_path, monkeypatch)
    import pytest

    from app.services.db_service import ProviderNotFound

    with pytest.raises(ProviderNotFound):
        db_service.update_provider(999, name="x", base_url="https://x/v1", api_key="k", models=["m"])


# ---------- 删除 ----------

def test_delete_provider_by_id(tmp_path, monkeypatch):
    _provider_db(tmp_path, monkeypatch)
    b = db_service.create_provider(name="b", base_url="https://x/v1", api_key="k", models=["m"])
    db_service.delete_provider(b["id"])
    names = [p["name"] for p in db_service.list_providers()]
    assert names == []


def test_delete_provider_selected_rejected(tmp_path, monkeypatch):
    SessionLocal = _provider_db(tmp_path, monkeypatch)
    import pytest

    from app.services.db_service import ProviderConflict

    db_service.create_provider(name="a", base_url="https://x/v1", api_key="k", models=["m"])
    db_service.create_provider(name="b", base_url="https://x/v1", api_key="k", models=["m"])
    _set_config(SessionLocal, "ai.provider", "a")
    with pytest.raises(ProviderConflict):
        db_service.delete_provider_by_name("a")


def test_delete_provider_not_found(tmp_path, monkeypatch):
    _provider_db(tmp_path, monkeypatch)
    import pytest

    from app.services.db_service import ProviderNotFound

    with pytest.raises(ProviderNotFound):
        db_service.delete_provider(999)


# ---------- 掩码工具 ----------

def test_mask_secret_various():
    assert config_service.mask_secret("") == ""
    assert config_service.mask_secret(None) == ""
    assert config_service.mask_secret("short") == "****"
    assert config_service.mask_secret("sk-1234567890abcdef") == "sk-****cdef"
    assert "1234567890" not in config_service.mask_secret("sk-1234567890abcdef")


# ---------- 配置 KV / get_ai_config ----------

def test_config_set_get_delete(tmp_path, monkeypatch):
    SessionLocal = _provider_db(tmp_path, monkeypatch)
    with SessionLocal() as s:
        config_service.set_config_value(s, "ai.model", "gpt-4o-mini")
        assert config_service.get_config_value(s, "ai.model") == "gpt-4o-mini"
        config_service.delete_config_value(s, "ai.model")
        assert config_service.get_config_value(s, "ai.model") == ""  # 恢复默认


def test_get_ai_config_references_provider(tmp_path, monkeypatch):
    SessionLocal = _provider_db(tmp_path, monkeypatch)
    cfg = AppConfig()
    cfg.llm = LLMConfig(
        providers={
            "default": ProviderConfig(name="default", base_url="http://x", api_key_env="FOO", model="m1"),
            "openai": ProviderConfig(name="openai", base_url="http://o", api_key_env="BAR", model="g"),
        },
        active_provider="default",
    )
    with SessionLocal() as s:
        if s.query(AiProvider).count() == 0:
            s.add_all(db_service._seed_providers(cfg))
            s.commit()
        _set_config(SessionLocal, "ai.provider", "openai")

    with SessionLocal() as s:
        ai = config_service.get_ai_config(s, cfg)
        assert ai.provider == "openai"
        assert ai.base_url == "http://o"
        assert ai.model == "g"  # default_model（无 ai.model 覆盖）
        assert ai.source == "database"

        # ai.model 覆盖
        config_service.set_config_value(s, "ai.model", "custom-model")
        ai2 = config_service.get_ai_config(s, cfg)
        assert ai2.model == "custom-model"
        assert ai2.source == "database"


def test_get_ai_config_custom_fallback(tmp_path, monkeypatch):
    SessionLocal = _provider_db(tmp_path, monkeypatch)
    cfg = AppConfig()
    cfg.llm = LLMConfig(
        providers={"default": ProviderConfig(name="default", base_url="http://x", api_key_env="FOO", model="m1")},
        active_provider="default",
    )
    with SessionLocal() as s:
        if s.query(AiProvider).count() == 0:
            s.add_all(db_service._seed_providers(cfg))
            s.commit()
        _set_config(SessionLocal, "ai.provider", "custom")

    with SessionLocal() as s:
        ai = config_service.get_ai_config(s, cfg)
        assert ai.provider == "custom"
        assert ai.base_url == "http://x"
        assert ai.model == "m1"
        assert ai.source == "config"


# ---------- 种子 ----------

def test_seed_writes_ai_providers_and_config(tmp_path, monkeypatch):
    SessionLocal = _provider_db(tmp_path, monkeypatch)
    cfg = AppConfig()
    cfg.llm = LLMConfig(
        providers={"default": ProviderConfig(name="default", base_url="http://x", api_key_env="FOO", model="m1")},
        active_provider="default",
    )
    with SessionLocal() as s:
        if s.query(AiProvider).count() == 0:
            s.add_all(db_service._seed_providers(cfg))
            s.commit()
        _seed_system_config(cfg, s)
        s.commit()
    with SessionLocal() as s:
        p = s.query(AiProvider).filter_by(name="default").first()
        assert p is not None and p.models == ["m1"]
        sel = s.query(SystemConfig).filter_by(key="ai.provider").first()
        assert sel is not None and sel.value == "default"
