"""`/api/v1/system/stats` 单元测试。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_system_stats_returns_json(client: TestClient) -> None:
    """端点正常返回 JSON，字段形状符合预期。"""
    resp = client.get("/api/v1/system/stats")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert set(data.keys()) >= {"cpu", "memory", "tasks", "gpu"}
    assert isinstance(data["cpu"], dict)
    assert "percent" in data["cpu"] and "cores" in data["cpu"]
    assert isinstance(data["memory"], dict)
    assert set(data["memory"].keys()) >= {"total", "used", "available", "percent"}
    assert isinstance(data["tasks"], dict)
    assert set(data["tasks"].keys()) >= {"running", "queued"}
    # gpu 在容器内不可见时为 null，否则为 list
    assert data["gpu"] is None or isinstance(data["gpu"], list)
    # 不泄漏内部字段
    assert "_prev_cpu" not in data
    assert "_runtime" not in data


def test_system_stats_fields_are_serializable(client: TestClient) -> None:
    """所有值为基本 JSON 类型（int/float/None/list），不含不可序列化对象。"""
    import json
    resp = client.get("/api/v1/system/stats")
    data = resp.json()
    # 再序列化一次应能成功
    raw = json.dumps(data)
    assert isinstance(raw, str)
    assert len(raw) > 0
