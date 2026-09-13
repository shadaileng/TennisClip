"""数据库引擎与会话管理（SQLAlchemy 多兼容层）。

默认 SQLite（零配置），可通过 config.yaml 的 database.url 切换到
PostgreSQL / MySQL（见各 driver 安装说明）。

用法：
    from app.db import get_engine, init_db, get_session
    engine = get_engine(config)
    init_db(engine)
    with get_session(engine) as s:
        ...
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import AppConfig
from app.utils.logger import get_logger

logger = get_logger(__name__)

_engines: dict[str, Engine] = {}


def _ensure_sqlite_dir(config: AppConfig, url: str) -> None:
    """SQLite 时确保父目录存在（相对路径基于 backend/ 根）。"""
    if url.startswith("sqlite"):
        # 形如 sqlite:///./data/tennisclip.db
        db_path = url.split("sqlite:////", 1)[-1] if "////" in url else url.split("sqlite:///", 1)[-1]
        if not db_path.startswith("/"):
            target = config.path("data")
            target.mkdir(parents=True, exist_ok=True)


def get_engine(config: AppConfig, use_pool: bool = True) -> Engine:
    """按 database.url 获取（并缓存）引擎。"""
    url = config.database.url
    _ensure_sqlite_dir(config, url)

    # 多线程环境下 SQLite 需允许跨线程；Postgres/MySQL 默认连接池即可
    kwargs: dict = {"echo": config.database.echo, "future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if use_pool:
            from sqlalchemy.pool import StaticPool
            # SQLite 单文件用 StaticPool 保证多线程共享同一连接
            kwargs["poolclass"] = StaticPool

    engine = create_engine(url, **kwargs)
    _engines[url] = engine
    logger.info("db: engine ready (%s)", _short_url(url))
    return engine


def _short_url(url: str) -> str:
    return url.split("@")[-1] if "@" in url else url


def init_db(engine: Engine) -> None:
    """建表（幂等）。导入 ORM 模型后调用 create_all。"""
    from app import db_models  # noqa: F401  确保模型注册
    from sqlalchemy.orm import sessionmaker

    db_models.Base.metadata.create_all(engine)
    # 初始化 model_providers 种子（若为空）
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    with SessionLocal() as session:
        if not session.query(db_models.ModelProvider).count():
            session.add_all(db_models._seed_providers())
            session.commit()
            logger.info("db: seeded model providers")


@contextmanager
def get_session(engine: Engine) -> Iterator[Session]:
    """会话上下文：自动 commit / rollback。"""
    from sqlalchemy.orm import sessionmaker

    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
