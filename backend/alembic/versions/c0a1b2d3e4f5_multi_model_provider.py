"""model_providers 演进为多模型 + 直存密钥结构

Revision ID: c0a1b2d3e4f5
Revises: b6d16d8e4957
Create Date: 2026-09-17 00:00:00.000000

演进内容（参考 TennisDiary 的 providers/models 设计）：
- 新增 api_key(Text)        直接存密钥明文
- 新增 models(Text/JSON)    模型列表（JSON 数组）
- 新增 selected_model       当前所选模型
- 新增 enabled / sort_order 启用开关 / 排序
- 删除 api_key_env / model  旧字段

数据迁移：旧 model -> models=[model]、selected_model=model、api_key=''；
旧库不保留明文密钥（原仅存环境变量名），由用户在管理页补填。
"""

from typing import Sequence, Union

import json

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c0a1b2d3e4f5"
down_revision: Union[str, Sequence[str], None] = "b6d16d8e4957"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1) 新增列（SQLite 用 batch 兼容）
    with op.batch_alter_table("model_providers") as batch:
        batch.add_column(sa.Column("api_key", sa.Text(), nullable=True))
        batch.add_column(sa.Column("models", sa.Text(), nullable=True))
        batch.add_column(sa.Column("selected_model", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")))

    # 2) 数据迁移：旧 model -> models / selected_model；api_key 留空
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT name, model FROM model_providers")).fetchall()
    for name, model in rows:
        if model:
            models_json = json.dumps([model], ensure_ascii=False)
            bind.execute(
                sa.text(
                    "UPDATE model_providers "
                    "SET api_key = :ak, models = :m, selected_model = :sm, enabled = 1, sort_order = 0 "
                    "WHERE name = :n"
                ),
                {"ak": "", "m": models_json, "sm": model, "n": name},
            )
    # 兜底：models 为空时置 '[]'，避免后续读取歧义
    bind.execute(
        sa.text("UPDATE model_providers SET models = '[]' WHERE models IS NULL OR models = ''")
    )

    # 3) 删除旧列
    with op.batch_alter_table("model_providers") as batch:
        batch.drop_column("api_key_env")
        batch.drop_column("model")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("model_providers") as batch:
        batch.add_column(sa.Column("api_key_env", sa.String(length=128), nullable=False, server_default=""))
        batch.add_column(sa.Column("model", sa.String(length=128), nullable=False, server_default=""))

    bind = op.get_bind()
    # 旧 model 由 selected_model / models[0] 还原；api_key_env 无法恢复（原仅存变量名），置空
    bind.execute(
        sa.text(
            "UPDATE model_providers SET model = COALESCE(selected_model, '') "
            "WHERE model = '' OR model IS NULL"
        )
    )

    with op.batch_alter_table("model_providers") as batch:
        batch.drop_column("api_key")
        batch.drop_column("models")
        batch.drop_column("selected_model")
        batch.drop_column("enabled")
        batch.drop_column("sort_order")
