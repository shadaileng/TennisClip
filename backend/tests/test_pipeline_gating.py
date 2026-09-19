"""管线阶段化依赖校验单元测试。

验证 core._validate_enabled_stages：
- 非法组合抛清晰 ValueError（高光/剪辑/报告依赖预处理；剪辑/报告依赖高光识别）
- 合法组合（含禁用子集、空集合）不抛错
"""

from __future__ import annotations

import pytest

from app.core import _validate_enabled_stages


def test_all_stages_valid():
    _validate_enabled_stages({"preprocess", "highlight", "edit", "report"})


def test_empty_stages_valid():
    _validate_enabled_stages(set())


def test_preprocess_only_valid():
    _validate_enabled_stages({"preprocess"})


def test_highlight_without_preprocess_invalid():
    with pytest.raises(ValueError, match="预处理"):
        _validate_enabled_stages({"highlight"})


def test_edit_without_preprocess_invalid():
    with pytest.raises(ValueError):
        _validate_enabled_stages({"preprocess", "edit"})  # 缺 highlight


def test_report_without_highlight_invalid():
    with pytest.raises(ValueError, match="高光识别"):
        _validate_enabled_stages({"preprocess", "report"})


def test_edit_without_highlight_invalid():
    # 预处理 + 高光识别 + 剪辑 是合法组合（单独验证，不抛错）
    _validate_enabled_stages({"preprocess", "highlight", "edit"})
    # 缺少 highlight 但启用 edit → 非法
    with pytest.raises(ValueError, match="高光识别"):
        _validate_enabled_stages({"preprocess", "edit"})


def test_preprocess_highlight_only_valid():
    # 合法子集：预处理 + 高光识别，跳过剪辑/报告
    _validate_enabled_stages({"preprocess", "highlight"})


def test_preprocess_report_skip_edit_valid():
    # 合法：预处理 + 高光识别 + 报告（跳过剪辑）
    _validate_enabled_stages({"preprocess", "highlight", "report"})
