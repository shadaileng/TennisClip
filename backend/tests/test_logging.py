"""TDD 测试：loguru 日志系统（双 sink / 统一格式 / uvicorn 拦截 / 级别来源 / 带参调用）。

遵循 docs/plans/02-后端loguru日志TDD方案.md 的 TC-01~TC-07。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.utils import logger as logger_mod
from app.utils.logger import (
    InterceptHandler,
    get_logger,
    setup_logging,
    teardown_logging,
)


@pytest.fixture(autouse=True)
def _reset_logging():
    """每个用例前后重置 loguru 单例与标准库 logging 全局状态，避免跨测试污染。

    仅重置 loguru 不足以隔离：第三方库（经 app.main 导入链）可能调用
    logging.disable(...) 抬高全局禁用级别，或直接设置 uvicorn.* logger 的
    level/handlers/propagate，导致本用例的 isEnabledFor 被短路、拦截失效。
    """

    def _reset_stdlib() -> None:
        # 复位全局禁用级别（logging.disable 的逆操作）
        logging.disable(0)
        # 复位被接管 logger 的 level / handlers / propagate / disabled，清掉第三方污染
        for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "fastapi"):
            lg = logging.getLogger(name)
            lg.setLevel(logging.NOTSET)
            lg.handlers = []
            lg.propagate = True
            lg.disabled = False
        # 清掉可能残留的 InterceptHandler，避免重复挂载
        root = logging.getLogger()
        root.handlers = [
            h for h in root.handlers if not isinstance(h, InterceptHandler)
        ]

    _reset_stdlib()
    teardown_logging()
    yield
    teardown_logging()
    _reset_stdlib()


@pytest.fixture
def tmp_log_file(tmp_path):
    """指向临时文件的日志环境，用例结束后清理 sink，避免 loguru 单例污染。"""
    log_file = tmp_path / "app.log"
    setup_logging(log_file=log_file)
    yield log_file
    teardown_logging()


def _flush():
    logger_mod._loguru_logger.complete()


# TC-01 返回 loguru 风格 logger（非标准库 Logger），且具备 bind
def test_get_logger_returns_loguru_logger():
    lg = get_logger("my_module")
    assert hasattr(lg, "info")
    assert hasattr(lg, "bind")
    assert hasattr(lg, "error")
    # 非标准库 logging.Logger
    assert not isinstance(lg, logging.Logger)


# TC-02 日志写入文件 sink（UTF-8 纯文本、统一格式）
def test_log_written_to_file_sink(tmp_log_file: Path):
    get_logger("svc").info("hello {}", "world")
    _flush()
    content = tmp_log_file.read_text(encoding="utf-8")
    assert "hello world" in content
    # 统一格式：时间 | 级别 | 模块:函数:行号 - 消息（级别按 {level: <8} 右补位）
    assert "INFO" in content
    assert "svc:" in content
    # 文件为纯文本（无 ANSI 转义）
    assert "\x1b[" not in content


# TC-03 级别来源：环境变量 > config.yaml > INFO
def test_level_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("TENNISCLIP_LOG_LEVEL", "DEBUG")
    setup_logging(log_file=tmp_path / "a.log")
    assert logger_mod._resolve_level() == "DEBUG"
    teardown_logging()


def test_level_default_info(monkeypatch, tmp_path):
    monkeypatch.delenv("TENNISCLIP_LOG_LEVEL", raising=False)
    monkeypatch.delenv("TENNISCLIP_CONFIG", raising=False)
    setup_logging(log_file=tmp_path / "c.log")
    assert logger_mod._resolve_level() == "INFO"
    teardown_logging()


# TC-04 标准 logging 被统一拦截（uvicorn 场景）
def test_stdlib_logging_intercepted(tmp_log_file: Path):
    logging.getLogger("uvicorn.access").info("GET /health")
    _flush()
    content = tmp_log_file.read_text(encoding="utf-8")
    assert "GET /health" in content
    # 经 InterceptHandler 汇入，沿用统一格式
    assert "INFO" in content


# TC-05 滚动文件参数生效（rotation / retention / compression）
def test_file_sink_rotation_params(tmp_log_file: Path):
    found = False
    for handler in logger_mod._loguru_logger._core.handlers.values():
        sink = handler._sink
        # 仅检查文件 sink（含 _rotation_function 等内部属性）
        if not hasattr(sink, "_rotation_function"):
            continue
        found = True
        assert sink._rotation_function is not None
        assert sink._retention_function is not None
        assert sink._compression_function is not None
    assert found, "未注册文件 sink（rotation/retention/compression）"


# TC-06 带参调用规范（延迟求值 {} + bind 结构化上下文）
def test_lazy_format_and_bind(tmp_log_file: Path):
    p, t = "/tmp/x.mp4", "task-1"
    get_logger("svc").info("path={} task_id={}", p, t)
    _flush()
    content = tmp_log_file.read_text(encoding="utf-8")
    assert f"path={p} task_id={t}" in content

    # bind() 注入的上下文进入 extra，可经 {extra[key]} 在格式中引用
    captured: list[str] = []
    lid = logger_mod._loguru_logger.add(
        lambda m: captured.append(str(m)),
        format="{extra[task_id]} | {message}",
        level="DEBUG",
    )
    get_logger("svc").bind(task_id=t).error("fail: {}", "boom")
    logger_mod._loguru_logger.complete()
    logger_mod._loguru_logger.remove(lid)
    assert any("task-1 | fail: boom" in c for c in captured)


# TC-07 时间为 UTC 并带 +00:00 偏移标志（便于按需换算本地时间）
def test_log_time_is_utc_with_offset(tmp_log_file: Path):
    import re
    from datetime import datetime, timezone

    get_logger("svc").info("utc check")
    _flush()
    first_line = tmp_log_file.read_text(encoding="utf-8").splitlines()[0]

    # 形如 2026-09-25 02:52:47.004+00:00 | INFO ...
    m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\+00:00 \| ", first_line)
    assert m, f"时间未带 UTC 偏移标志：{first_line}"
    # 换算为 UTC 后与当前 UTC 时间相差不超过 5 秒（排除跨秒误差）
    logged = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S.%f").replace(
        tzinfo=timezone.utc
    )
    assert abs((datetime.now(timezone.utc) - logged).total_seconds()) < 5
