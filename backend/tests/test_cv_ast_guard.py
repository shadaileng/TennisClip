"""AST 静态守卫：禁止 cv_* 模块出现 `/ 255.0` 后紧跟 `.to(...)` 的表达式。

根因背景：Python 属性访问优先级高于除法——
    x.float() / 255.0
    .to(dev)
会被解析成 `x.float() / (255.0.to(dev))`，而 float 没有 .to 方法，
运行时抛 `'float' object has no attribute 'to'`（曾在 tracknet 实跑中炸掉级联）。
正确写法是把 `.to(dev)` 放在除法之前。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

UTILS_DIR = Path(__file__).resolve().parent.parent / "app" / "utils"
CV_FILES = sorted(UTILS_DIR.glob("cv_*.py"))


def _bad_div_to_calls(tree: ast.AST) -> list[ast.Call]:
    """找出 right 侧形如 `255.0.to(...)` 的 BinOp 除法调用。"""
    hits: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Div):
            continue
        right = node.right
        if isinstance(right, ast.Call) and isinstance(right.func, ast.Attribute):
            if isinstance(right.func.value, ast.Constant) and isinstance(
                right.func.value.value, float
            ):
                hits.append(right)
    return hits


def test_cv_files_exist() -> None:
    assert CV_FILES, "未找到 cv_*.py，路径可能变了"


@pytest.mark.parametrize("path", CV_FILES, ids=lambda p: p.name)
def test_no_float_literal_method_chaining(path: Path) -> None:
    """cv_* 源码中不得出现 `<float字面量>.<method>(...)` 形式的链式调用。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits = _bad_div_to_calls(tree)
    assert not hits, (
        f"{path.name} 中发现 float 字面量被调用 .to() 等方法（行 "
        f"{[h.lineno for h in hits]}）——属性访问优先级高于除法，"
        f"把 .to(dev) 移到 / 255.0 之前"
    )
