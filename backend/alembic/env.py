"""Alembic 迁移环境。

数据库连接串与 ORM 元数据统一来自项目配置（app.config.load_config）：
- 连接串：config.database.url（受环境变量 DATABASE_URL 覆盖，优先级最高）
- 目标元数据：app.db_models.Base.metadata（autogenerate 用于 diff 表结构）

这样无需在 alembic.ini 硬编码 sqlalchemy.url，保持与运行时代码同源。
用法：
    cd backend
    uv run alembic upgrade head            # 升级到最新
    uv run alembic revision --autogenerate # 依据 ORM 模型生成新迁移
    uv run alembic downgrade -1            # 回退一步
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 目标元数据（autogenerate 用于 diff）
from app.db_models import Base

target_metadata = Base.metadata


def _resolve_database_url() -> str:
    """解析数据库连接串：环境变量 DATABASE_URL > 项目 config.yaml > 默认。

    与 app.config.load_config 保持一致的优先级，保证迁移与运行时同源。
    """
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        return env_url

    # 从项目配置读取（config.yaml + .env + 默认值）
    from app.config import load_config

    return load_config().database.url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = _resolve_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    url = _resolve_database_url()

    # SQLite 需与运行时一致：允许跨线程；单文件用 StaticPool
    connectable = None
    if url.startswith("sqlite"):
        from app.db import get_engine

        from app.config import load_config

        connectable = get_engine(load_config())
        engine_from_config_kwargs: dict = {}
    else:
        engine_from_config_kwargs = dict(
            {"sqlalchemy.url": url},
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
        connectable = engine_from_config(engine_from_config_kwargs)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
