"""旧配置键别名兼容测试（b352955 重命名遗留的 DB 孤儿行）。

- 读：新键无覆盖行时回退旧键（highlight.level / llm.analysis_mode）
- 写：set 时孤儿行原地迁移为新键，不残留旧键
- 删：delete 同时清理新旧两键的覆盖行
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.db_models  # noqa: F401  确保 ORM 注册
from app.db_models import Base, SystemConfig
from app.services import config_service


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'cfg.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _add_legacy(s, key: str, value: str) -> None:
    s.add(SystemConfig(key=key, value=value))
    s.commit()


def test_get_config_value_falls_back_to_legacy_key(tmp_path):
    SessionLocal = _session(tmp_path)
    with SessionLocal() as s:
        _add_legacy(s, "highlight.level", "all")
        _add_legacy(s, "llm.analysis_mode", "video")
        assert config_service.get_config_value(s, "llm.analysis_level") == "all"
        assert config_service.get_config_value(s, "llm.highlight_strategy") == "video"


def test_new_key_takes_priority_over_legacy(tmp_path):
    SessionLocal = _session(tmp_path)
    with SessionLocal() as s:
        _add_legacy(s, "highlight.level", "all")
        _add_legacy(s, "llm.analysis_level", "beginner")
        assert config_service.get_config_value(s, "llm.analysis_level") == "beginner"


def test_build_config_list_surfaces_legacy_value(tmp_path):
    SessionLocal = _session(tmp_path)
    with SessionLocal() as s:
        _add_legacy(s, "highlight.level", "all")
        items = {it["key"]: it for it in config_service.build_config_list(s)}
        assert items["llm.analysis_level"]["value"] == "all"
        assert items["llm.analysis_level"]["source"] == "db"


def test_set_config_migrates_legacy_row(tmp_path):
    SessionLocal = _session(tmp_path)
    with SessionLocal() as s:
        _add_legacy(s, "highlight.level", "all")
        config_service.set_config_value(s, "llm.analysis_level", "beginner")
        keys = [r.key for r in s.query(SystemConfig).all()]
        assert "llm.analysis_level" in keys
        assert "highlight.level" not in keys, "孤儿行应原地迁移，不残留旧键"
        assert config_service.get_config_value(s, "llm.analysis_level") == "beginner"


def test_set_default_clears_legacy_row(tmp_path):
    """写入等于默认值时删行——旧键孤儿行也要一并清掉，否则回退读会顶回来。"""
    SessionLocal = _session(tmp_path)
    with SessionLocal() as s:
        _add_legacy(s, "highlight.level", "all")
        config_service.set_config_value(s, "llm.analysis_level", "intermediate")
        assert s.query(SystemConfig).filter_by(key="highlight.level").first() is None
        assert config_service.get_config_value(s, "llm.analysis_level") == "intermediate"


def test_delete_clears_both_keys(tmp_path):
    SessionLocal = _session(tmp_path)
    with SessionLocal() as s:
        _add_legacy(s, "highlight.level", "all")
        _add_legacy(s, "llm.analysis_level", "beginner")
        config_service.delete_config_value(s, "llm.analysis_level")
        assert s.query(SystemConfig).count() == 0, "新旧两键覆盖行都应被清理"
        assert config_service.get_config_value(s, "llm.analysis_level") == "intermediate"
