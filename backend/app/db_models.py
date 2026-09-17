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

import json
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    TypeDecorator,
    ForeignKey,
)
from sqlalchemy.orm import declarative_base, Mapped, mapped_column, relationship

Base = declarative_base()


class JSONList(TypeDecorator):
    """Text 列存 JSON 数组，Python 侧透明为 list[str]；对空/非 list 容错返回 []。

    参考 TennisDiary 的 models 字段实现：数据库统一存 JSON 文本，应用层以 list 操作。
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return "[]"
        if isinstance(value, str):
            return value
        return json.dumps(list(value), ensure_ascii=False)

    def process_result_value(self, value, dialect) -> list[str]:
        if not value:
            return []
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        if not isinstance(parsed, list):
            return []
        return [str(m).strip() for m in parsed if str(m).strip()]


class AiProvider(Base):
    """模型服务商（OpenAI 兼容三要素 + 每商多模型），纯凭据目录。

    - api_key 直接入库明文（本地单机工具，非多租户）；接口返回掩码。
    - models 为模型列表，default_model 取首项。
    - enabled（int 0/1）控制是否可被直选引用；sort_order 控制列表展示顺序。
    - 激活服务商与选定模型不再存于本表，改由 system_config 的 ai.provider / ai.model 配置覆盖。
    """

    __tablename__ = "ai_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    base_url: Mapped[str] = mapped_column(String(512))
    api_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    models: Mapped[list[str]] = mapped_column(JSONList, default=list)
    enabled: Mapped[int] = mapped_column(Integer, default=1)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    @property
    def default_model(self) -> str:
        """默认模型：models 首项；空列表时为 ''。"""
        return self.models[0] if self.models else ""

    def __repr__(self) -> str:
        return f"<AiProvider {self.name} enabled={self.enabled}>"


class SystemConfig(Base):
    """动态配置 KV（system_config）：仅存覆盖值，无覆盖行即默认值。

    当前用于 ai.provider（激活服务商）/ ai.model（覆盖模型）/ ai.api_key / ai.base_url。
    """

    __tablename__ = "system_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<SystemConfig {self.key}>"


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


def _seed_providers(config: "AppConfig") -> list[AiProvider]:
    """从传入 config 的 providers 列表导入种子（若表为空）。

    密钥不随静态配置入库（api_key 留空），由用户在管理页填写；
    models 优先取 ProviderConfig.models，缺省回退单 model 包装为列表。
    is_active / selected_model 已移除：激活服务商改由 system_config.ai.provider 配置。
    """
    providers = []
    for name, p in config.llm.providers.items():
        seed_models = list(p.models) if getattr(p, "models", None) else ([p.model] if p.model else [])
        providers.append(
            AiProvider(
                name=name,
                base_url=p.base_url,
                api_key="",
                models=seed_models,
                enabled=1,
                sort_order=0,
            )
        )
    return providers


def _seed_system_config(config: "AppConfig", session) -> None:
    """初始化 system_config 种子（若 ai.provider 尚无覆盖行）。

    激活服务商默认取 config.llm.active_provider，使首启动即有选中项。
    """
    from sqlalchemy import func

    existing = session.query(func.count(SystemConfig.id)).filter_by(key="ai.provider").scalar()
    if existing:
        return
    active = config.llm.active_provider if config.llm.providers else "custom"
    session.add(SystemConfig(key="ai.provider", value=active))
