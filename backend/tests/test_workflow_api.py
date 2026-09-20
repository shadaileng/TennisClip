"""Step 6 · 路由与 API — TC-44~TC-48。

测试 /api/v1/workflows 系列端点。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import app.workflow.nodes  # noqa: F401 — 触发注册


# ──────────── helpers ────────────


def _make_graph_dict():
    """构造一个合法工作流图 dict（使用 compile_from_legacy 默认图）。"""
    from app.workflow.presets import compile_from_legacy
    return compile_from_legacy().to_dict()


def _setup_api(tmp_path, monkeypatch):
    """设置测试数据库并返回 TestClient。"""
    from sqlalchemy.orm import sessionmaker

    from app.db import get_engine, init_db
    from app.db_models import Base, Workflows
    from app.services import db_service
    from app.config import load_config
    from app.main import app

    cfg = load_config()
    test_engine = get_engine(cfg)
    init_db(test_engine)
    SessionLocal = sessionmaker(bind=test_engine, expire_on_commit=False)
    monkeypatch.setattr(db_service, "_SessionLocal", SessionLocal)
    # 清理旧数据
    with SessionLocal() as s:
        s.query(Workflows).delete()
        s.commit()
    return TestClient(app)


# ──────────── TC-44 ────────────
class TestTC44WorkflowSchema:
    """GET /api/v1/workflows/schema 返回全部节点，可 JSON 解析且含参数默认值与选项。"""

    def test_schema_endpoint(self, tmp_path, monkeypatch):
        client = _setup_api(tmp_path, monkeypatch)
        resp = client.get("/api/v1/workflows/schema")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0
        # 每个节点含 type / inputs / outputs / params
        for node in data:
            assert "type" in node
            assert "inputs" in node
            assert "outputs" in node
            assert "params" in node

    def test_schema_params_have_defaults(self, tmp_path, monkeypatch):
        client = _setup_api(tmp_path, monkeypatch)
        resp = client.get("/api/v1/workflows/schema")
        data = resp.json()
        # 找到 analyze.highlight 节点，检查其 params 有 default
        analyze = next((n for n in data if n["type"] == "analyze.highlight"), None)
        assert analyze is not None
        level_param = next((p for p in analyze["params"] if p["key"] == "level"), None)
        assert level_param is not None
        assert level_param["default"] == "intermediate"
        assert "beginner" in level_param["options"]


# ──────────── TC-45 ────────────
class TestTC45CreateWorkflow:
    """POST /api/v1/workflows 合法图落库；非法图 400 且 detail 含具体校验原因。"""

    def test_create_valid_workflow(self, tmp_path, monkeypatch):
        client = _setup_api(tmp_path, monkeypatch)
        resp = client.post("/api/v1/workflows", json={
            "name": "API新建",
            "graph": _make_graph_dict(),
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "API新建"
        assert data["id"] > 0

    def test_create_invalid_graph_returns_400(self, tmp_path, monkeypatch):
        client = _setup_api(tmp_path, monkeypatch)
        bad_graph = {
            "version": 1,
            "name": "非法图",
            "nodes": [
                {"id": "n1", "type": "nonexistent.type", "params": {}},
            ],
            "edges": [],
        }
        resp = client.post("/api/v1/workflows", json={
            "name": "非法图",
            "graph": bad_graph,
        })
        assert resp.status_code == 400
        assert "校验" in resp.json()["detail"] or "非法" in resp.json()["detail"]


# ──────────── TC-46 ────────────
class TestTC46BuiltinProtection:
    """PUT 内置工作流 → 403；DELETE 内置/激活中 → 409。"""

    def test_update_builtin_returns_403(self, tmp_path, monkeypatch):
        client = _setup_api(tmp_path, monkeypatch)
        # 创建内置工作流
        resp = client.post("/api/v1/workflows", json={
            "name": "内置",
            "graph": _make_graph_dict(),
            "is_builtin": True,
        })
        wf_id = resp.json()["id"]
        # 尝试更新
        resp = client.put(f"/api/v1/workflows/{wf_id}", json={"name": "改名"})
        assert resp.status_code == 403

    def test_delete_builtin_returns_409(self, tmp_path, monkeypatch):
        client = _setup_api(tmp_path, monkeypatch)
        resp = client.post("/api/v1/workflows", json={
            "name": "内置删",
            "graph": _make_graph_dict(),
            "is_builtin": True,
        })
        wf_id = resp.json()["id"]
        resp = client.delete(f"/api/v1/workflows/{wf_id}")
        assert resp.status_code == 409


# ──────────── TC-47 ────────────
class TestTC47ActivateWorkflow:
    """POST /api/v1/workflows/{id}/activate 生效。"""

    def test_activate_and_list(self, tmp_path, monkeypatch):
        client = _setup_api(tmp_path, monkeypatch)
        # 创建两个工作流
        resp1 = client.post("/api/v1/workflows", json={"name": "A", "graph": _make_graph_dict()})
        resp2 = client.post("/api/v1/workflows", json={"name": "B", "graph": _make_graph_dict()})
        id_a = resp1.json()["id"]
        id_b = resp2.json()["id"]

        # 激活 B
        resp = client.post(f"/api/v1/workflows/{id_b}/activate")
        assert resp.status_code == 200

        # 列表中 B 标记为 active
        resp = client.get("/api/v1/workflows")
        wfs = resp.json()
        wf_b = next(w for w in wfs if w["id"] == id_b)
        assert wf_b.get("is_active") is True or wf_b.get("active") is True


# ──────────── TC-48 ────────────
class TestTC48ValidateDraft:
    """POST /api/v1/workflows/validate 校验草稿但不落库（列表长度不变）。"""

    def test_validate_valid_draft(self, tmp_path, monkeypatch):
        client = _setup_api(tmp_path, monkeypatch)
        resp = client.post("/api/v1/workflows/validate", json={"graph": _make_graph_dict()})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

    def test_validate_invalid_draft(self, tmp_path, monkeypatch):
        client = _setup_api(tmp_path, monkeypatch)
        bad_graph = {
            "version": 1,
            "name": "非法",
            "nodes": [
                {"id": "n1", "type": "nonexistent.type", "params": {}},
            ],
            "edges": [],
        }
        resp = client.post("/api/v1/workflows/validate", json={"graph": bad_graph})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert len(data["errors"]) > 0

    def test_validate_does_not_persist(self, tmp_path, monkeypatch):
        client = _setup_api(tmp_path, monkeypatch)
        # 获取初始列表长度
        resp_before = client.get("/api/v1/workflows")
        count_before = len(resp_before.json())

        # 提交验证
        client.post("/api/v1/workflows/validate", json={"graph": _make_graph_dict()})

        # 列表长度不变
        resp_after = client.get("/api/v1/workflows")
        count_after = len(resp_after.json())
        assert count_after == count_before
