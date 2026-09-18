"""日志工具（基于 loguru）。

统一规范：
- 双输出：控制台（stderr，彩色）+ 滚动文件（backend/data/app.log）。
- 统一格式：时间 | 级别 | 模块:函数:行号 - 消息。
- 经 InterceptHandler 接管标准 logging（uvicorn / FastAPI），全链路风格一致。
- 级别来源：环境变量 TENNISCLIP_LOG_LEVEL > config.yaml 的 logging.level > INFO。
- 调用约定：延迟求值 `{}` 占位符；结构化上下文用 `logger.bind(key=value)`。

对外保持 get_logger(name) 接口兼容，现有调用点无需改动。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from loguru import logger as _loguru_logger

from app.config import load_config

# 统一格式规范（时间 | 级别 | 模块:函数:行号 - 消息）
# 文件 sink 使用纯文本（无 ANSI 转义，便于 grep / 归档）
FMT_TEXT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
    "{extra[name]}:{function}:{line} - {message}"
)
# 控制台 sink 增加颜色标签（colorize=True 时生效）
FMT_CONSOLE = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | "
    "<cyan>{extra[name]}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
)

_configured = False

# 需统一接管的标准库 logger（uvicorn / FastAPI 内置）
_STDLIB_LOGGERS = ("uvicorn", "uvicorn.access", "uvicorn.error", "fastapi")


def _resolve_level() -> str:
    """级别解析：环境变量 TENNISCLIP_LOG_LEVEL > config.yaml 的 logging.level > INFO。

    注意：TENNISCLIP_LOG_LEVEL 通常写在 .env 中，而 .env 由 load_config() 内部的
    load_dotenv 注入 os.environ。因此必须先调用 load_config() 将 .env 载入环境，
    再读取环境变量；否则在 setup_logging() 早于 load_config() 的启动顺序下，
    .env 里的 DEBUG 设置会被忽略（实测表现为 .env 不生效）。
    """
    try:
        load_config()  # 触发 .env 加载（幂等，override=False），注入 os.environ
    except Exception:  # noqa: BLE001
        pass
    env = os.environ.get("TENNISCLIP_LOG_LEVEL")
    if env:
        return env.upper()
    try:
        return str(load_config().logging_level).upper()
    except Exception:
        return "INFO"


class InterceptHandler(logging.Handler):
    """将标准库 logging 记录转接到 loguru，实现全链路统一格式。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = _loguru_logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        # 将标准库 logger 名称注入 extra[name]，使统一格式对拦截日志同样可用
        _loguru_logger.bind(name=record.name).opt(
            depth=6, exception=record.exc_info
        ).log(level, record.getMessage())


def _intercept_stdlib_logging() -> None:
    """挂载 InterceptHandler 到 logging.root，并接管 uvicorn / FastAPI logger。"""
    # 复位第三方库可能设置的全局禁用级别（如 logging.disable(WARNING)），
    # 否则其会令 isEnabledFor 短路，使低级别日志（含 uvicorn access）被静默丢弃。
    logging.disable(0)
    # 清空已注册的标准 handler，统一经 loguru 输出（force=True 覆盖既有配置）
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    for name in _STDLIB_LOGGERS:
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
        lg.setLevel(0)  # NOTSET，避免被第三方抬高等级而丢弃低级别日志
        lg.disabled = False  # 复位第三方可能设置的禁用标记（如 uvicorn 静默）
    # root 级别置 0，由 loguru 自行按 sink 级别过滤，避免标准层提前丢弃
    logging.getLogger().setLevel(0)


def setup_logging(log_file: Path | None = None) -> None:
    """幂等初始化 loguru 双 sink 与标准 logging 拦截。

    log_file 可选（默认 config.log_file_path，即 data_dir/app.log），测试可指向临时文件。
    """
    global _configured
    if _configured:
        return

    level = _resolve_level()
    _loguru_logger.remove()  # 清除默认 sink，防止重复输出

    # 控制台 sink（stderr，彩色）
    _loguru_logger.add(
        sys.stderr,
        level=level,
        colorize=True,
        format=FMT_CONSOLE,
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )

    # 文件 sink（滚动 + 压缩，纯文本便于归档）
    if log_file is None:
        log_file = load_config().log_file_path
    try:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        _loguru_logger.add(
            str(log_file),
            level=level,
            colorize=False,
            format=FMT_TEXT,
            encoding="utf-8",
            rotation="10 MB",
            retention="7 days",
            compression="zip",
            enqueue=True,
        )
    except Exception as exc:  # noqa: BLE001
        _loguru_logger.warning("文件日志初始化失败，仅启用控制台输出：{}", exc)

    _intercept_stdlib_logging()
    _configured = True


def teardown_logging() -> None:
    """清理已注册 sink 并复位状态（主要用于测试隔离）。"""
    global _configured
    _loguru_logger.remove()
    _configured = False


def get_logger(name: str, level: str | None = None):
    """返回绑定模块名的 loguru logger，兼容原 logging 用法。

    level 参数保留以兼容历史签名，实际级别由 setup_logging 统一控制。
    """
    if not _configured:
        setup_logging()
    return _loguru_logger.bind(name=name)
