"""回归关卡：带参调用规范（禁止 % 风格 / f-string / 字符串拼接，强制 {} 占位符）。

直接复用 backend/scripts/check_logging.py 的静态校验，确保日志约定不被回退。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_logging.py"


def test_logging_call_convention():
    result = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"日志带参调用规范校验失败（退出码 {result.returncode}）：\n"
        f"{result.stdout}{result.stderr}"
    )
