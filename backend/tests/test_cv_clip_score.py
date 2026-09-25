"""`cv_clip_score._extract_embeds` 兼容性测试（transformers 4.x/5.x 返回类型）。

背景：transformers 5.17 的 ``CLIPModel.get_image_features`` / ``get_text_features``
不再直接返回 tensor，而是返回 ``BaseModelOutputWithPooling``（其 ``pooler_output``
已被替换为投影后的嵌入）。旧代码 ``img_f / img_f.norm(...)`` 会抛
``'BaseModelOutputWithPooling' object has no attribute 'norm'``，炸掉
``post.score_highlights`` 节点。本文件锁住提取逻辑的各条分支。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.utils.cv_clip_score import _extract_embeds
from app.utils.cv_runtime import CvUnavailable

torch = pytest.importorskip("torch")


def test_tensor_passthrough() -> None:
    """transformers 4.x 路径：直接返回 tensor，原样透传。"""
    t = torch.randn(4, 512)
    assert _extract_embeds(t) is t


def test_model_output_uses_projected_pooler() -> None:
    """transformers 5.x 路径：取 pooler_output（已投影的嵌入）。"""
    pooled = torch.randn(4, 512)
    last_hidden = torch.randn(4, 197, 768)
    out = SimpleNamespace(last_hidden_state=last_hidden, pooler_output=pooled)
    got = _extract_embeds(out)
    assert got is pooled
    assert got.shape == (4, 512)


def test_embeds_attrs_fallback() -> None:
    """CLIPOutput 风格：pooler_output 缺失时回退 image_embeds / text_embeds。"""
    emb = torch.randn(2, 512)
    assert _extract_embeds(SimpleNamespace(image_embeds=emb)) is emb
    assert _extract_embeds(SimpleNamespace(text_embeds=emb)) is emb


def test_tuple_form_takes_last_element() -> None:
    """ModelOutput 元组形式 (last_hidden_state, pooler_output) → 取末位嵌入。"""
    pooled = torch.randn(3, 512)
    last_hidden = torch.randn(3, 197, 768)
    assert _extract_embeds((last_hidden, pooled)) is pooled


def test_none_pooler_falls_through_to_embeds() -> None:
    """pooler_output 为 None（占位）时应继续找嵌入字段，而不是返回 None。"""
    emb = torch.randn(1, 512)
    out = SimpleNamespace(pooler_output=None, image_embeds=emb)
    assert _extract_embeds(out) is emb


def test_unknown_output_raises_cv_unavailable() -> None:
    """无法提取时抛 CvUnavailable（post 节点按方案 fail，附可读原因）。"""
    with pytest.raises(CvUnavailable):
        _extract_embeds(SimpleNamespace(foo=1))
    with pytest.raises(CvUnavailable):
        _extract_embeds("not-a-model-output")


def test_real_clip_output_shape_contract() -> None:
    """用真实 transformers 类型验证：5.x 返回对象时能取出投影后嵌入。

    不加载权重（避免联网），直接构造该类型实例。
    """
    transformers = pytest.importorskip("transformers")
    try:
        from transformers.modeling_outputs import BaseModelOutputWithPooling
    except ImportError:  # pragma: no cover - 版本差异
        pytest.skip("该版本无 BaseModelOutputWithPooling")
    pooled = torch.randn(2, 512)
    out = BaseModelOutputWithPooling(
        last_hidden_state=torch.randn(2, 77, 512), pooler_output=pooled
    )
    got = _extract_embeds(out)
    assert got is pooled
    # 必须可直接做归一化运算（即修掉的 AttributeError 路径）
    normed = got / got.norm(dim=-1, keepdim=True)
    assert torch.allclose(normed.norm(dim=-1), torch.ones(2), atol=1e-5)
    assert transformers.__version__
