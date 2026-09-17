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
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, inspect as sa_inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import AppConfig
from app.utils.logger import get_logger

logger = get_logger(__name__)

_engines: dict[str, Engine] = {}

# backend/ 根目录（db.py 位于 backend/app/db.py）
_BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _ensure_sqlite_dir(config: AppConfig, url: str) -> None:
    """SQLite 时确保父目录存在（相对路径基于 backend/ 根）。"""
    if url.startswith("sqlite"):
        # 形如 sqlite:///./data/tennisclip.db
        db_path = url.split("sqlite:////", 1)[-1] if "////" in url else url.split("sqlite:///", 1)[-1]
        if not db_path.startswith("/"):
            target = config.data_path
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
    logger.info("db: engine ready ({})", _short_url(url))
    return engine


def _short_url(url: str) -> str:
    return url.split("@")[-1] if "@" in url else url


def init_db(engine: Engine) -> None:
    """建表（幂等）：优先 Alembic 迁移，create_all 兜底。

    - 库中已有 alembic_version 表 → 直接 alembic upgrade head 升到最新
    - 库中没有 alembic_version（首次 / 旧库）→ 先 create_all 建表，再 stamp head
    - 已有业务表（旧 create_all 产物）→ stamp head，避免重复建表
    创建后初始化 model_providers 种子（若为空）。
    """
    from app import db_models  # noqa: F401  确保模型注册
    from sqlalchemy.orm import sessionmaker

    _apply_migrations_or_create(engine)

    # 初始化 ai_providers / system_config 种子（若为空）
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    with SessionLocal() as session:
        from app.config import load_config

        if not session.query(db_models.AiProvider).count():
            session.add_all(db_models._seed_providers(load_config()))
            session.commit()
            logger.info("db: seeded ai providers")
        if not session.query(db_models.SystemConfig).count():
            db_models._seed_system_config(load_config(), session)
            session.commit()
            logger.info("db: seeded system config")


def _apply_migrations_or_create(engine: Engine) -> None:
    """优先 Alembic 迁移；对无 alembic_version 的库做 create_all + stamp。"""
    from app import db_models

    inspector = sa_inspect(engine)
    has_alembic = "alembic_version" in inspector.get_table_names()

    if has_alembic:
        # 已有迁移版本表：升级到 head（幂等，无新 revision 时为 no-op）
        _run_alembic("upgrade")
        logger.info("db: alembic upgrade head applied")
    else:
        # 首次或旧库（create_all 建的，无 alembic_version）：
        # 幂等建表后把当前 head 版本 stamp 进去，后续 schema 演进走迁移
        db_models.Base.metadata.create_all(engine)
        _run_alembic("stamp")
        logger.info("db: schema ensured via create_all, stamped to alembic head")


def _run_alembic(action: str) -> None:
    """在进程内执行 alembic upgrade/stamp head。

    env.py 自行解析连接串（DATABASE_URL > config.yaml > 默认），
    与运行时代码同源；SQLite 复用 get_engine 的 StaticPool 缓存。
    """
    from alembic.config import Config
    from alembic import command as alembic_command

    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    if action == "upgrade":
        alembic_command.upgrade(cfg, "head")
    else:
        alembic_command.stamp(cfg, "head")


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
