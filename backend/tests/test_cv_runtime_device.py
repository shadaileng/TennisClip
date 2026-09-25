"""cv_runtime 设备解析单元测试：resolve_device + ensure_device 类型守卫。"""

from __future__ import annotations

import pytest

from app.utils.cv_runtime import ensure_device, resolve_device


class TestResolveDevice:
    """resolve_device 返回值永远是合法字符串。"""

    def test_auto_returns_valid(self) -> None:
        dev = resolve_device("auto")
        assert dev in ("cuda", "cpu"), f"意外返回值：{dev!r}"

    def test_cpu_returns_cpu(self) -> None:
        assert resolve_device("cpu") == "cpu"

    def test_cuda_when_available(self) -> None:
        import torch
        if torch.cuda.is_available():
            assert resolve_device("cuda") == "cuda"
        else:
            # 无 CUDA 时显式请求 cuda 仍回退 cpu（不抛异常）
            assert resolve_device("cuda") == "cpu"

    def test_gpu_alias(self) -> None:
        assert resolve_device("gpu") == resolve_device("cuda")

    def test_invalid_falls_back(self) -> None:
        # 非法字符串回退 auto 语义（即跟 auto 相同）
        assert resolve_device("banana") == resolve_device("auto")

    def test_empty_string_falls_back(self) -> None:
        assert resolve_device("") == resolve_device("auto")

    def test_none_falls_back(self) -> None:
        assert resolve_device(None) == resolve_device("auto")  # type: ignore[arg-type]


class TestEnsureDevice:
    """ensure_device 对非 str 输入强制兜底为 auto 语义，绝不返回非 str。"""

    def test_str_passthrough(self) -> None:
        assert isinstance(ensure_device("auto"), str)
        assert isinstance(ensure_device("cpu"), str)
        assert isinstance(ensure_device("cuda"), str)

    def test_float_rejected(self) -> None:
        result = ensure_device(3.14)
        assert isinstance(result, str)
        assert result in ("cuda", "cpu")

    def test_none_rejected(self) -> None:
        result = ensure_device(None)  # type: ignore[arg-type]
        assert isinstance(result, str)
        assert result in ("cuda", "cpu")

    def test_object_rejected(self) -> None:
        class Fake:
            pass
        result = ensure_device(Fake())
        assert isinstance(result, str)
        assert result in ("cuda", "cpu")

    def test_zero_rejected(self) -> None:
        # 0 有时被误当作 "cpu"（ultralytics 默认），这里不应被放行
        result = ensure_device(0)
        assert isinstance(result, str)
        assert result in ("cuda", "cpu")

    def test_returns_same_as_auto_for_any_input(self) -> None:
        """任何输入都回退到与 'auto' 相同的结果——保证一致性。"""
        base = resolve_device("auto")
        for bad in (None, 0, 3.14, [], {}, object()):  # type: ignore[arg-type]
            assert ensure_device(bad) == base, f"输入 {bad!r} 未回退到 auto"
