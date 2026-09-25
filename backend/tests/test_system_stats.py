"""`/api/v1/system/stats` 单元测试。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.utils import system_stats as ss


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


# ---------- CPU 差分计算（回归：禁止用累计比值冒充实时利用率） ----------


def test_diff_cgroup_uses_counter_delta() -> None:
    """cgroup 模式按 usage_usec 差分换算：2s 内消耗 2s CPU / 8 核 → 12.5%。"""
    prev = (100.0, "cgroup", {"usage_usec": 1_000_000})
    cur = ("cgroup", {"usage_usec": 3_000_000})  # Δ = 2_000_000us = 2s CPU
    pct = ss._diff_cpu_percent(prev, cur, quota_cores=8.0, now=102.0)
    assert pct == 12.5  # 2s / 2s墙钟 / 8核


def test_diff_proc_uses_counter_delta_not_cumulative() -> None:
    """proc 模式必须用增量：累计比值会算出 90.2%，增量才是 97.5%。"""
    prev = (100.0, "proc", {"idle": 1000, "total": 10000})  # 历史累计 90% 空闲
    cur = ("proc", {"idle": 1005, "total": 10200})  # 窗口内 Δidle=5/Δtotal=200
    pct = ss._diff_cpu_percent(prev, cur, quota_cores=None, now=101.0)
    assert pct == 97.5
    assert pct != round(100.0 * (1.0 - 1005 / 10200), 1)  # 非累计比值


def test_diff_rejects_short_window_and_kind_mismatch() -> None:
    """采样窗口过短（噪声）或数据源切换时返回 None。"""
    prev = (100.0, "cgroup", {"usage_usec": 1_000_000})
    cur = ("cgroup", {"usage_usec": 2_000_000})
    assert ss._diff_cpu_percent(prev, cur, quota_cores=4.0, now=100.01) is None
    assert ss._diff_cpu_percent(prev, ("proc", {"idle": 1, "total": 2}),
                                quota_cores=4.0, now=110.0) is None


def test_diff_handles_counter_reset_and_clamping() -> None:
    """计数器回退（cgroup 重建）返回 None；超额瞬时值钳到 0~100。"""
    prev = (100.0, "cgroup", {"usage_usec": 5_000_000})
    assert ss._diff_cpu_percent(prev, ("cgroup", {"usage_usec": 1_000_000}),
                                quota_cores=2.0, now=110.0) is None
    # 1s 内消耗 4s CPU / 1核 → 400% → 钳到 100
    prev2 = (100.0, "cgroup", {"usage_usec": 0})
    pct = ss._diff_cpu_percent(prev2, ("cgroup", {"usage_usec": 4_000_000}),
                               quota_cores=1.0, now=101.0)
    assert pct == 100.0


def test_first_sample_builds_baseline_then_reports_percent() -> None:
    """首次采样只建基线（percent=None），之后才产出差分值。"""
    saved = ss._PREV_CPU
    ss._PREV_CPU = None
    try:
        first = ss.get_system_stats()
        assert first["cpu"]["percent"] is None
        assert ss._PREV_CPU is not None, "首次调用必须写入基线"
    finally:
        ss._PREV_CPU = saved


# ---------- cgroup 解析（容器真实配额，非宿主全局） ----------


def test_cgroup_cpu_quota_if_available() -> None:
    quota = ss._cgroup_cpu_quota()
    if quota is None:
        pytest.skip("当前环境 cgroup 无 CPU 配额（cpu.max 全链为 max）")
    assert quota > 0
    assert quota <= 1024, f"配额异常：{quota} 核"


def test_cgroup_memory_if_available() -> None:
    mem = ss._cgroup_memory()
    if mem is None:
        pytest.skip("当前环境 cgroup 无内存限制")
    used, total = mem
    assert total > 0 and used >= 0
    assert used <= total * 1.5  # 容器突发允许略超，但不应离谱


def test_cgroup_chain_is_self_first() -> None:
    """链路近者优先且以挂载根收尾（祖先回溯语义）。"""
    chain = ss._cgroup_chain()
    assert chain, "cgroup 链不得为空"
    assert str(chain[-1]) == "/sys/fs/cgroup"
    if len(chain) > 1:
        assert str(chain[0]).startswith(str(chain[-1]))
        assert chain[0] != chain[-1]

