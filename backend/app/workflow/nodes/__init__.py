"""节点实现：薄封装既有 services（不变其业务逻辑）。

每个节点函数统一签名：run(ctx, params) -> dict[port, value]
"""

from __future__ import annotations

from app.workflow.nodes import (
    input_video,
    preprocess_transcode,
    detect_candidates,
    analyze_highlight,
    post_filter,
    post_uniform,
    edit_concat,
    report_technical,
    output_artifact,
)


def _register_all_nodes() -> None:
    """注册所有内置节点到 spec 注册表。

    导入即注册（每个模块顶部的 @register 装饰器完成注册），
    本函数确保所有模块被加载。
    """
    pass  # 模块导入已在顶部完成，注册已自动触发
