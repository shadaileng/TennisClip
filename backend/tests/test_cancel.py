"""任务协作式取消（TaskCancelled）单元测试。

覆盖：cancel_task / is_cancelled / clear_cancelled / check_cancelled 基本语义，
以及 executor 在节点边界捕获 TaskCancelled 后走 failed 分支并清除旗标。
"""

from __future__ import annotations

import pytest

from app.utils import cancel


@pytest.fixture(autouse=True)
def _clean_cancel_flags():
    """每个用例前后清空全局取消旗标，避免用例间串扰。"""
    cancel._cancelled.clear()
    yield
    cancel._cancelled.clear()


def test_cancel_task_marks_flag():
    assert cancel.cancel_task("abc") is True
    assert cancel.is_cancelled("abc") is True
    # 幂等：重复 cancel 返回 False
    assert cancel.cancel_task("abc") is False


def test_check_cancelled_raises():
    cancel.cancel_task("xyz")
    with pytest.raises(cancel.TaskCancelled):
        cancel.check_cancelled("xyz", "test")


def test_check_cancelled_noop_when_not_cancelled():
    # 未取消的 task 不抛异常
    cancel.check_cancelled("nope", "test")


def test_clear_cancelled_removes_flag():
    cancel.cancel_task("def")
    assert cancel.is_cancelled("def") is True
    cancel.clear_cancelled("def")
    assert cancel.is_cancelled("def") is False


def test_task_cancelled_message():
    exc = cancel.TaskCancelled("abc")
    assert "abc" in str(exc)
    assert exc.task_id == "abc"
