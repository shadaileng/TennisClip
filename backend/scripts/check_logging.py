#!/usr/bin/env python
"""日志带参调用规范静态校验（检验标准）。

扫描 backend/app 下的日志调用，强制以下约定（详见 AGENTS.md 日志约定）：
- 禁止 f-string：要求用 loguru 延迟求值 `{}` 占位符
- 禁止 % 风格占位符：%s / %d / %f / %(name)s 等
- 禁止字符串拼接："a" + "b"

用法：
    uv run python backend/scripts/check_logging.py
退出码：0=无违规，1=存在违规，2=扫描目录不存在。

该脚本仅依赖标准库，可在 CI 中独立执行；同时被
tests/test_logging_convention.py 在 `uv run pytest` 时回归拦截。
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

LOGGER_METHODS = {"debug", "info", "warning", "error", "critical", "exception"}
# 匹配 %s %d %f %r %(name)s 等旧式占位符（注意：单独的 "100%" 不会被误判）
PCT_RE = re.compile(r"%(?:\([^)]*\))?[sdfr]")

VIOLATIONS: list[tuple[Path, int, str]] = []


def _receiver_name(node: ast.AST) -> str | None:
    """取调用接收者名称，形如 logger / self.logger / x.logger -> 'logger'。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def is_logger_call(node: ast.Call) -> bool:
    f = node.func
    if not isinstance(f, ast.Attribute) or f.attr not in LOGGER_METHODS:
        return False
    return _receiver_name(f.value) == "logger"


def scan_file(path: Path) -> None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as e:
        VIOLATIONS.append((path, 0, f"语法错误: {e}"))
        return

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and is_logger_call(node)):
            continue
        if not node.args:
            continue
        msg = node.args[0]
        if isinstance(msg, ast.JoinedStr):
            VIOLATIONS.append((path, msg.lineno, "禁止使用 f-string，请用 {} 占位符（延迟求值）"))
        elif isinstance(msg, ast.Constant) and isinstance(msg.value, str):
            if PCT_RE.search(msg.value):
                VIOLATIONS.append((path, msg.lineno, "禁止使用 % 风格占位符，请用 {} 占位符"))
        elif isinstance(msg, ast.BinOp) and isinstance(msg.op, ast.Add):
            VIOLATIONS.append((path, msg.lineno, "禁止字符串拼接，请用 {} 占位符"))


def find_backend_root() -> Path:
    # backend/scripts/check_logging.py -> 上两级为 backend/
    return Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description="日志带参调用规范校验")
    parser.add_argument("--root", type=Path, default=None, help="扫描根目录（默认 backend/app）")
    args = parser.parse_args()

    backend = find_backend_root()
    root = args.root or (backend / "app")
    if not root.exists():
        print(f"扫描目录不存在: {root}", file=sys.stderr)
        return 2

    for py in sorted(root.rglob("*.py")):
        if "tests" in py.parts:  # 测试文件本身可用任意形式，跳过
            continue
        scan_file(py)

    if VIOLATIONS:
        print("发现日志规范违规：")
        for p, ln, reason in VIOLATIONS:
            rel = p.relative_to(backend) if str(p).startswith(str(backend)) else p
            print(f"  {rel}:{ln}  {reason}")
        return 1

    print(f"日志规范校验通过（已扫描 {root}）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
