"""pytest 全局 fixtures：测试环境与隔离引导。

作用：
- 启动时设置 TENNISCLIP_ENV=test，使所有 load_config() 自动加载 backend/.env.test，
  与开发/生产配置彻底隔离（不触碰真实 data/ 目录）。
- 会话级 fixture 预建测试数据目录（data_test/），确保 SQLite 连接前父目录已存在。

测试隔离是「配置问题而非代码问题」：同一套业务代码，仅通过 .env.test 把数据目录、
数据库连接串、API Key 等重定向到测试世界，无需任何业务分支。
"""

from __future__ import annotations

import os

# 测试环境彻底隔离：标记后 load_config() 自动选 .env.test
os.environ.setdefault("TENNISCLIP_ENV", "test")


def pytest_configure() -> None:
    """pytest 启动时确保测试数据目录存在。

    放在 pytest_configure 而非模块顶层，避免被非 pytest 场景（如 IDE 自动导入）误触发。
    """
    from app.config import load_config

    load_config().ensure_data_dir()
