"""数据库表结构（SQLAlchemy ORM）。

表设计（兼容 SQLite / PostgreSQL / MySQL）：
- tasks        任务记录（task_id/状态/耗时/错误）
- task_inputs  输入记录（视频路径/大小/时长/分析层级）
- task_outputs 输出记录（集锦路径/报告路径/文件信息）
- task_results 结果快照（高光 JSON、报告 JSON 全文）
- model_providers 模型提供商配置（支持运行时动态切换）
- files        文件管理（所有处理文件的状态跟踪）
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    ForeignKey,
)
from sqlalchemy.orm import declarative_base, Mapped, mapped_column, relationship

Base = declarative_base()


class ModelProvider(Base):
    """模型提供商（OpenAI 兼容三要素），支持数据库动态切换。"""

    __tablename__ = "model_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    base_url: Mapped[str] = mapped_column(String(512))
    api_key_env: Mapped[str] = mapped_column(String(128), default="")
    model: Mapped[str] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        return f"<ModelProvider {self.name} active={self.is_active}>"


class Task(Base):
    """一条视频处理任务的完整记录。"""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    level: Mapped[str] = mapped_column(String(32), default="intermediate")
    source_video: Mapped[str] = mapped_column(String(512), default="")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    elapsed_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    inputs: Mapped[list["TaskInput"]] = relationship(back_populates="task")
    outputs: Mapped[list["TaskOutput"]] = relationship(back_populates="task")
    result_snapshot: Mapped[Optional["TaskResult"]] = relationship(
        back_populates="task", uselist=False
    )

    def __repr__(self) -> str:
        return f"<Task {self.task_id} {self.status}>"


class TaskInput(Base):
    """输入记录：视频文件元信息。"""

    __tablename__ = "task_inputs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tasks.task_id"), index=True
    )
    file_path: Mapped[str] = mapped_column(String(512))
    file_name: Mapped[str] = mapped_column(String(256), default="")
    file_size_mb: Mapped[float] = mapped_column(Float, default=0.0)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    fps: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    level: Mapped[str] = mapped_column(String(32), default="intermediate")

    task: Mapped["Task"] = relationship(back_populates="inputs")

    def __repr__(self) -> str:
        return f"<TaskInput {self.task_id} {self.file_name}>"


class TaskOutput(Base):
    """输出记录：生成的集锦/报告文件信息。"""

    __tablename__ = "task_outputs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tasks.task_id"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))  # highlight_video | report
    file_path: Mapped[str] = mapped_column(String(512))
    file_size_mb: Mapped[float] = mapped_column(Float, default=0.0)
    target_duration: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    task: Mapped["Task"] = relationship(back_populates="outputs")

    def __repr__(self) -> str:
        return f"<TaskOutput {self.task_id} {self.kind}>"


class TaskResult(Base):
    """结果快照：高光识别 + 分析报告全文（JSON 列）。"""

    __tablename__ = "task_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tasks.task_id"), unique=True
    )
    highlight_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    report_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    generated_by: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    task: Mapped["Task"] = relationship(back_populates="result_snapshot")

    def __repr__(self) -> str:
        return f"<TaskResult {self.task_id}>"


class FileRecord(Base):
    """文件管理：所有处理涉及的文件状态跟踪。"""

    __tablename__ = "files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_path: Mapped[str] = mapped_column(String(512), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # input | preprocessed | highlight | report | uploaded
    task_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("tasks.task_id"), nullable=True
    )
    size_mb: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="created")  # created | consumed | removed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    removed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<FileRecord {self.kind} {self.file_path}>"


def _seed_providers(config: "AppConfig") -> list[ModelProvider]:
    """从传入 config 的 providers 列表导入种子（若表为空）。"""
    providers = []
    for name, p in config.llm.providers.items():
        providers.append(
            ModelProvider(
                name=name,
                base_url=p.base_url,
                api_key_env=p.api_key_env,
                model=p.model,
                is_active=(name == config.llm.active_provider),
            )
        )
    return providers
