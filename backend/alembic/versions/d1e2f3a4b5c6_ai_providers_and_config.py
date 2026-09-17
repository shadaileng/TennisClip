"""model_providers → ai_providers 重构，并新增 system_config 配置表

Revision ID: d1e2f3a4b5c6
Revises: c0a1b2d3e4f5
Create Date: 2026-09-17 00:00:00.000000

演进内容（对齐 TennisDiary）：
- 新增 system_config 表（key/value KV，存 ai.provider / ai.model 等覆盖）
- model_providers 改名为 ai_providers（纯凭据目录）
- 删除 is_active / selected_model 列（激活与选模型改由 system_config.ai.provider / ai.model 承担）
- enabled 经 ORM 改为 Integer（SQLite 存储层已为 0/1，无需改列类型）
- 原 is_active 服务商写入 system_config.ai.provider，保留首启动选中项
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "c0a1b2d3e4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()

    # 1) 迁移前读取原激活服务商名（is_active 仍存在于 model_providers）
    active_name = None
    try:
        row = bind.execute(
            sa.text("SELECT name FROM model_providers WHERE is_active = 1 LIMIT 1")
        ).fetchone()
        if row is not None:
            active_name = row[0]
    except Exception:  # noqa: BLE001
        # 旧表无 is_active（极旧库）时忽略
        active_name = None

    # 2) 新建 system_config 表
    op.create_table(
        "system_config",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    op.create_index("ix_system_config_key", "system_config", ["key"], unique=True)

    # 3) model_providers → ai_providers
    op.rename_table("model_providers", "ai_providers")

    # 4) 删除 is_active / selected_model 列
    with op.batch_alter_table("ai_providers") as batch:
        batch.drop_column("is_active")
        batch.drop_column("selected_model")

    # 5) 写入首启动选中服务商（若有）
    if active_name:
        bind.execute(
            sa.text(
                "INSERT OR IGNORE INTO system_config (key, value) "
                "VALUES ('ai.provider', :n)"
            ),
            {"n": active_name},
        )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()

    # 1) ai_providers 恢复 is_active / selected_model
    with op.batch_alter_table("ai_providers") as batch:
        batch.add_column(sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("selected_model", sa.String(length=128), nullable=True))

    # 2) 把 ai.provider 还原为 is_active=1（其余置 0）
    try:
        row = bind.execute(
            sa.text("SELECT value FROM system_config WHERE key = 'ai.provider' LIMIT 1")
        ).fetchone()
        provider_name = row[0] if row is not None else None
    except Exception:  # noqa: BLE001
        provider_name = None
    if provider_name:
        bind.execute(sa.text("UPDATE ai_providers SET is_active = 0"))
        bind.execute(
            sa.text("UPDATE ai_providers SET is_active = 1 WHERE name = :n"),
            {"n": provider_name},
        )

    # 3) 删除 system_config 表
    op.drop_index("ix_system_config_key")
    op.drop_table("system_config")

    # 4) ai_providers → model_providers
    op.rename_table("ai_providers", "model_providers")
