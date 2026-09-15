"""配置管理：环境变量 > .env > config.yaml > 代码默认值（优先级高→低）。

分层原则：
- 敏感 / 环境相关（API Key、数据库连接串）→ 一律走环境变量（P0），
  由 .env（gitignore，P1）或容器/K8s/CI 直接注入（P0）。
- 非敏感运行参数 + 各配置块默认值 → config.yaml（P2，随代码入库）。
- 终极兜底 → dataclass 默认值（P3）。

模型配置采用 providers（提供商）模式：
- 每个 provider 统一 OpenAI 兼容格式：base_url / api_key_env / model
- active_provider 指定当前启用的提供商（默认 "default"）
- model_providers 表已入库，支持运行时动态切换（见 app.services.db_service）

数据库多兼容：
- 默认 SQLite（零配置单文件），可切 PostgreSQL / MySQL
- database.url 解析顺序：环境变量 DATABASE_URL > .env > config.yaml > 代码默认
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class ProviderConfig:
    """单个模型提供商（OpenAI 兼容协议格式）。"""
    name: str = ""
    base_url: str = ""
    api_key_env: str = ""
    model: str = ""


@dataclass
class LLMConfig:
    """LLM 模型配置（providers 模式 + 采样参数）。"""
    providers: dict = field(default_factory=dict)
    active_provider: str = "default"
    temperature: float = 0.2
    max_tokens: int = 4096
    timeout_seconds: int = 60
    mock_mode: str = "auto"

    def resolve(self) -> ProviderConfig:
        """按 active_provider 解析出当前生效的提供商。"""
        provider = self.providers.get(self.active_provider)
        if provider is None:
            raise ValueError(
                f"active_provider '{self.active_provider}' 未在 llm.providers 中定义"
            )
        return provider

    @property
    def base_url(self) -> str:
        return self.resolve().base_url

    @property
    def model(self) -> str:
        return self.resolve().model


@dataclass
class VideoConfig:
    resolution: str = "720p"
    fps: int = 30
    bitrate: str = "2M"
    sample_rate: int = 30
    max_input_seconds: int = 300


@dataclass
class HighlightConfig:
    target_duration: int = 15
    max_segments: int = 3
    min_segment_seconds: int = 3


@dataclass
class ReportConfig:
    levels: list = field(default_factory=lambda: ["beginner", "intermediate", "professional"])
    language: str = "zh"


@dataclass
class QueueConfig:
    max_concurrent_tasks: int = 2
    task_timeout_seconds: int = 120


@dataclass
class DatabaseConfig:
    """多数据库兼容：默认 SQLite，可切 PostgreSQL / MySQL。

    url 为 SQLAlchemy 连接串，运行时由 config.yaml 的 database.url 覆盖。
    SQLite 零配置单文件（./data/tennisclip.db）；
    Postgres/MySQL 示例：
      postgresql+psycopg://user:pass@127.0.0.1:5432/tennisclip
      mysql+pymysql://user:pass@127.0.0.1:3306/tennisclip
    """
    url: str = "sqlite:///./data/tennisclip.db"
    echo: bool = False


@dataclass
class PathsConfig:
    """目录布局：输入/输出/日志/数据库统一归入 data_dir 之下。

    sample_dir / output_dir 均相对 data_dir（默认 data/sample_videos、data/outputs）；
    日志固定写入 data_dir/app.log，SQLite 落 data_dir/tennisclip.db。
    """

    sample_dir: str = "sample_videos"
    output_dir: str = "outputs"
    data_dir: str = "data"


@dataclass
class CorsConfig:
    """跨域配置（前后端分开部署时使用）。

    生产环境前后端不同源时，前端跨域请求需后端放行对应源。
    allowed_origins 为允许的来源列表；"*" 表示放行所有源
    （仅推荐开发 / 内网演示；生产应配置具体源列表）。
    默认 "*" 保证开箱即用，生产通过 config.yaml / 环境变量 CORS_ALLOWED_ORIGINS 收紧。
    """
    allowed_origins: list = field(default_factory=lambda: ["*"])


@dataclass
class AppConfig:
    logging_level: str = "INFO"
    llm: LLMConfig = field(default_factory=LLMConfig)
    video: VideoConfig = field(default_factory=VideoConfig)
    highlight: HighlightConfig = field(default_factory=HighlightConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    queue: QueueConfig = field(default_factory=QueueConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    cors: CorsConfig = field(default_factory=CorsConfig)
    root: Path = _PROJECT_ROOT

    @property
    def active_provider(self) -> ProviderConfig:
        """当前生效的模型提供商（OpenAI 兼容格式）。"""
        return self.llm.resolve()

    @property
    def api_key(self) -> str:
        """从环境变量读取 API Key（变量名由当前 provider 的 api_key_env 指定）。

        优先级：进程环境变量 > .env（load_dotenv 注入）> 空。
        敏感凭据一律不落 config.yaml。
        """
        provider = self.active_provider
        return os.environ.get(provider.api_key_env, "")

    def path(self, name: str) -> Path:
        """解析相对路径到项目根目录下的绝对路径。"""
        return self.root / name

    @property
    def data_path(self) -> Path:
        """统一数据目录：输入/输出/日志/数据库均在此之下。"""
        return self.root / self.paths.data_dir

    @property
    def sample_path(self) -> Path:
        """示例输入视频目录（data_dir/sample_dir）。"""
        return self.data_path / self.paths.sample_dir

    @property
    def output_path(self) -> Path:
        """处理结果输出目录（data_dir/output_dir）。"""
        return self.data_path / self.paths.output_dir

    @property
    def log_file_path(self) -> Path:
        """日志文件路径（data_dir/app.log）。"""
        return self.data_path / "app.log"

    def ensure_output_dir(self) -> Path:
        out = self.output_path
        out.mkdir(parents=True, exist_ok=True)
        return out


def _build_provider_list(data: list[dict]) -> dict:
    """将 YAML 中的 providers 列表转为 name → ProviderConfig 字典。"""
    result: dict = {}
    for item in data:
        provider = ProviderConfig(
            name=item.get("name", ""),
            base_url=item.get("base_url", ""),
            api_key_env=item.get("api_key_env", ""),
            model=item.get("model", ""),
        )
        result[provider.name] = provider
    return result


def load_config(config_file: Optional[str | Path] = None) -> AppConfig:
    """加载配置：环境变量 > .env > config.yaml > 代码默认值。

    load_dotenv(override=False)：.env 只补充尚未设置的变量，
    已存在的进程环境变量（P0，容器/K8s/CI 注入）优先于 .env（P1）。
    """
    load_dotenv(_PROJECT_ROOT / ".env", override=False)

    cfg_path = Path(config_file) if config_file else _PROJECT_ROOT / "config.yaml"
    data: dict = {}
    if cfg_path.exists():
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    config = AppConfig()

    def fill(dc, section: dict | None) -> None:
        if not section:
            return
        for key, value in section.items():
            if hasattr(dc, key):
                current = getattr(dc, key)
                if isinstance(current, dict) and isinstance(value, dict):
                    fill(current, value)
                elif isinstance(current, (list, tuple, set)) and isinstance(value, list):
                    setattr(dc, key, value)
                else:
                    setattr(dc, key, value)

    # llm 节：providers 列表单独解析，其余字段直接填充
    llm_section = data.get("llm", {})
    if llm_section.get("providers"):
        config.llm.providers = _build_provider_list(llm_section["providers"])
    fill(config.llm, {k: v for k, v in llm_section.items() if k != "providers"})
    fill(config.video, data.get("video"))
    fill(config.highlight, data.get("highlight"))
    fill(config.report, data.get("report"))
    fill(config.queue, data.get("queue"))
    fill(config.paths, data.get("paths"))
    fill(config.database, data.get("database"))
    fill(config.cors, data.get("cors"))

    # P0/P1 覆盖：环境变量 DATABASE_URL 优先于 yaml 的 database.url
    # （.env 由 load_dotenv 注入 os.environ；容器/K8s/CI 直接设进程环境变量）
    db_url_env = os.environ.get("DATABASE_URL")
    if db_url_env:
        config.database.url = db_url_env

    # P0/P1 覆盖：环境变量 CORS_ALLOWED_ORIGINS（逗号分隔）优先于 yaml 的 cors.allowed_origins
    cors_env = os.environ.get("CORS_ALLOWED_ORIGINS")
    if cors_env:
        config.cors.allowed_origins = [o.strip() for o in cors_env.split(",") if o.strip()]

    # P0/P1 覆盖：环境变量 TENNISCLIP_DATA_DIR 优先于 yaml 的 paths.data_dir
    data_dir_env = os.environ.get("TENNISCLIP_DATA_DIR")
    if data_dir_env:
        config.paths.data_dir = data_dir_env

    if data.get("logging", {}).get("level"):
        config.logging_level = data["logging"]["level"]

    return config
