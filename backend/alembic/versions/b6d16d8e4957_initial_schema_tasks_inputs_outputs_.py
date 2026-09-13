"""initial schema: tasks/inputs/outputs/results/providers/files

Revision ID: b6d16d8e4957
Revises:
Create Date: 2026-09-13 20:49:24.375758

六张表与 ORM 模型（app.db_models）一一对应：
- tasks / task_inputs / task_outputs / task_results / model_providers / files
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b6d16d8e4957"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 表依赖顺序：tasks → 子表（task_inputs/outputs/results/files 外键指向 tasks.task_id）
    # → model_providers（独立表）。

    op.create_table(
        "model_providers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=False),
        sa.Column("api_key_env", sa.String(length=128), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_model_providers_name", "model_providers", ["name"], unique=True)

    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("level", sa.String(length=32), nullable=False),
        sa.Column("source_video", sa.String(length=512), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("elapsed_seconds", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tasks_task_id", "tasks", ["task_id"], unique=True)
    op.create_index("ix_tasks_status", "tasks", ["status"], unique=False)

    op.create_table(
        "task_inputs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(length=32), nullable=False),
        sa.Column("file_path", sa.String(length=512), nullable=False),
        sa.Column("file_name", sa.String(length=256), nullable=False),
        sa.Column("file_size_mb", sa.Float(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("fps", sa.Float(), nullable=True),
        sa.Column("level", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.task_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_inputs_task_id", "task_inputs", ["task_id"], unique=False)

    op.create_table(
        "task_outputs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("file_path", sa.String(length=512), nullable=False),
        sa.Column("file_size_mb", sa.Float(), nullable=False),
        sa.Column("target_duration", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.task_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_outputs_task_id", "task_outputs", ["task_id"], unique=False)

    op.create_table(
        "task_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(length=32), nullable=False),
        sa.Column("highlight_json", sa.Text(), nullable=True),
        sa.Column("report_json", sa.Text(), nullable=True),
        sa.Column("generated_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.task_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id"),
    )

    op.create_table(
        "files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.String(length=512), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("task_id", sa.String(length=32), nullable=True),
        sa.Column("size_mb", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("removed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.task_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_files_file_path", "files", ["file_path"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_files_file_path", table_name="files")
    op.drop_table("files")
    op.drop_table("task_results")
    op.drop_index("ix_task_outputs_task_id", table_name="task_outputs")
    op.drop_table("task_outputs")
    op.drop_index("ix_task_inputs_task_id", table_name="task_inputs")
    op.drop_table("task_inputs")
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_index("ix_tasks_task_id", table_name="tasks")
    op.drop_table("tasks")
    op.drop_index("ix_model_providers_name", table_name="model_providers")
    op.drop_table("model_providers")
