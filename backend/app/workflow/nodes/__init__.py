"""节点实现：薄封装既有 services（不变其业务逻辑）。

每个节点函数统一签名：run(ctx, params) -> dict[port, value]

批次 A4：自动发现注册（文档 12 · Step 0.4）。
不再维护手工 import 清单——扫描本包内所有模块并导入，
模块顶部的 @register 装饰器即完成注册。新增节点放文件即生效，
无需改动本文件（类型名重复仍抛 ValueError，见 spec.register）。
"""

from __future__ import annotations

import importlib
import pkgutil

# 自动发现导入：包内每个模块导入一次，@register 装饰器完成注册。
# 模块名保持小写下划线命名约定；排序保证确定性（与执行器日志可复现要求一致）。
for _name in sorted(m.name for m in pkgutil.iter_modules(__path__)):
    importlib.import_module(f"{__name__}.{_name}")
