"""启动环境自检：FFMPEG / 数据库 / 模型提供商 / 数据目录。

run_startup_checks(config) 在 FastAPI 启动阶段调用一次，聚合为结构化结果；
任一检查失败仅打印 logger.warning、不终止进程（用户选择「仅警告继续」）。

检查分级：
- fail（硬性依赖缺失）：FFMPEG 未安装、数据库不可连、数据目录不可写、
  模型提供商无法解析 / 关键字段缺失 / 无 API Key 且不可降级。
- warn（可降级）：模型提供商未配 API Key 但 mock_mode=='auto'，可回退 Mock 运行。
- ok：通过。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from sqlalchemy import text

from app.utils.logger import get_logger

if TYPE_CHECKING:
    from app.config import AppConfig

logger = get_logger(__name__)


@dataclass
class CheckResult:
    """单项检查结果。status: ok | warn | fail。"""

    name: str
    status: str
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


def _short_url(url: str) -> str:
    return url.split("@")[-1] if "@" in url else url


def check_ffmpeg() -> CheckResult:
    """校验系统依赖 ffmpeg / ffprobe 是否可用。"""
    from app.utils import ffmpeg

    try:
        available = ffmpeg.is_available()
    except Exception as exc:  # noqa: BLE001
        return CheckResult("ffmpeg", "fail", f"探测异常：{exc}")
    if available:
        return CheckResult("ffmpeg", "ok", "ffmpeg / ffprobe 可用")
    return CheckResult("ffmpeg", "fail", "未检测到 ffmpeg / ffprobe，视频处理将不可用")


def check_database(config: "AppConfig") -> CheckResult:
    """按 config.database.url 实际建立连接并执行 SELECT 1 ping。"""
    from app.db import get_engine

    try:
        engine = get_engine(config)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return CheckResult("database", "ok", f"连接成功（{_short_url(config.database.url)}）")
    except Exception as exc:  # noqa: BLE001
        return CheckResult("database", "fail", f"连接失败（{_short_url(config.database.url)}）：{exc}")


def check_provider(config: "AppConfig") -> CheckResult:
    """校验 active_provider 可解析、关键字段非空，无 API Key 时确认可降级。"""
    try:
        provider = config.active_provider
    except ValueError as exc:
        return CheckResult("provider", "fail", str(exc))

    missing = [
        field
        for field, value in (("base_url", provider.base_url), ("model", provider.model))
        if not value
    ]
    if missing:
        return CheckResult(
            "provider", "fail",
            f"提供商 {provider.name} 缺失字段：{', '.join(missing)}",
        )

    if config.api_key:
        return CheckResult("provider", "ok", f"提供商 {provider.name} 已配置 API Key")

    # 无 API Key：依赖 mock 模式降级
    mock_mode = str(config.llm.mock_mode).lower()
    if mock_mode == "auto":
        return CheckResult(
            "provider", "warn",
            f"提供商 {provider.name} 未配置 API Key，将回退 Mock 模式（mock_mode=auto）",
        )
    return CheckResult(
        "provider", "fail",
        f"提供商 {provider.name} 未配置 API Key 且 mock_mode={config.llm.mock_mode}，无法运行",
    )


def check_data_dir(config: "AppConfig") -> CheckResult:
    """校验 data 目录可创建且可写（SQLite 父目录 / 输出 / 日志）。"""
    try:
        data = config.ensure_data_dir()
    except Exception as exc:  # noqa: BLE001
        return CheckResult("data_dir", "fail", f"数据目录创建失败：{exc}")

    probe = data / ".write_test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception as exc:  # noqa: BLE001
        return CheckResult("data_dir", "fail", f"数据目录不可写（{data}）：{exc}")
    return CheckResult("data_dir", "ok", f"数据目录可写（{data}）")


def run_startup_checks(config: "AppConfig") -> dict[str, CheckResult]:
    """执行全部启动环境检查，返回 name -> CheckResult 映射（不抛异常）。"""
    checks: dict[str, CheckResult] = {
        "ffmpeg": check_ffmpeg(),
        "database": check_database(config),
        "provider": check_provider(config),
        "data_dir": check_data_dir(config),
    }
    for result in checks.values():
        if result.status != "ok":
            logger.warning("启动环境检查未通过 [{}]：{}", result.name, result.detail)

    all_ok = all(c.status == "ok" for c in checks.values())
    summary = "全部通过" if all_ok else "存在告警项"
    logger.info(
        "启动环境检查完成（{}）：{}",
        summary,
        {name: c.status for name, c in checks.items()},
    )
    return checks
