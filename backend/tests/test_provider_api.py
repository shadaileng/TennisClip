"""模型服务商 CRUD / 配置 / check-models /health 接口集成测试（对齐 TennisDiary）。

用 FastAPI TestClient 驱动真实请求路径；db_service 引擎被替换为临时 SQLite，
与真实 data/ 隔离。覆盖：
- GET /api/v1/db/providers（空 / 含数据 / api_key 掩码 / is_selected）
- POST /api/v1/db/providers（新增 / 重复 409 / 非法 base_url 400 / 空 models 400）
- PUT /api/v1/db/providers/{id}（编辑 / api_key 留空保留）
- DELETE /api/v1/db/providers/{id}（成功 / 被 ai.provider 引用 409）
- GET/PUT/DELETE /api/v1/config（ai.provider 切换、ai.model 覆盖、选项动态）
- POST /api/v1/db/providers/check-models（list / probe 探测）
- /health 即时反映 ai.provider / ai.model 配置
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db_models  # noqa: F401
from app.db_models import Base


def _make_engine(tmp_path):
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{tmp_path / 'data' / 'tennisclip.db'}"
    engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'bootstrap.db'}")
    import app.main
    from app.services import db_service

    engine = _make_engine(tmp_path)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(db_service, "_engine", engine)
    monkeypatch.setattr(db_service, "_SessionLocal", SessionLocal)
    from fastapi.testclient import TestClient

    return TestClient(app.main.app)


def _payload(**over):
    base = {
        "name": "openai",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-1234567890abcdef",
        "models": ["gpt-4o", "gpt-4o-mini"],
        "enabled": True,
        "sort_order": 0,
    }
    base.update(over)
    return base


def test_list_providers_empty(client):
    resp = client.get("/api/v1/db/providers")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_and_list(client):
    r = client.post("/api/v1/db/providers", json=_payload(name="openai"))
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "openai"
    assert body["models"] == ["gpt-4o", "gpt-4o-mini"]
    assert body["enabled"] is True
    assert "sk-1234567890abcdef" not in body["api_key"]  # 掩码

    listed = client.get("/api/v1/db/providers").json()
    assert len(listed) == 1
    assert listed[0]["name"] == "openai"
    assert "sk-1234567890abcdef" not in listed[0]["api_key"]


def test_create_duplicate_409(client):
    client.post("/api/v1/db/providers", json=_payload(name="dup"))
    r = client.post("/api/v1/db/providers", json=_payload(name="dup", base_url="https://y/v1"))
    assert r.status_code == 409


def test_create_invalid_base_url_400(client):
    r = client.post("/api/v1/db/providers", json=_payload(name="bad", base_url="ftp://x"))
    assert r.status_code == 400


def test_create_empty_models_400(client):
    r = client.post("/api/v1/db/providers", json=_payload(name="bad", models=["", " "]))
    assert r.status_code == 400


def test_update_provider(client):
    created = client.post("/api/v1/db/providers", json=_payload(name="p")).json()
    r = client.put(
        f"/api/v1/db/providers/{created['id']}",
        json={
            "name": "p",
            "base_url": "https://x/v1",
            "api_key": "",
            "models": ["m3", "m4"],
            "enabled": True,
            "sort_order": 0,
        },
    )
    assert r.status_code == 200
    assert r.json()["models"] == ["m3", "m4"]
    assert r.json()["api_key"] == "sk-****cdef"  # 留空保留原值（掩码）


def test_update_provider_not_found(client):
    r = client.put(
        "/api/v1/db/providers/999",
        json=_payload(name="x"),
    )
    assert r.status_code == 404


def test_delete_inactive_ok(client):
    created = client.post("/api/v1/db/providers", json=_payload(name="a")).json()
    client.post("/api/v1/db/providers", json=_payload(name="b"))
    r = client.delete(f"/api/v1/db/providers/{created['id']}")
    assert r.status_code == 200
    names = [p["name"] for p in client.get("/api/v1/db/providers").json()]
    assert names == ["b"]


def test_delete_selected_409(client):
    a = client.post("/api/v1/db/providers", json=_payload(name="a")).json()
    client.post("/api/v1/db/providers", json=_payload(name="b"))
    # 把 ai.provider 指向 a
    resp = client.put("/api/v1/config/ai.provider", json={"value": "a"})
    assert resp.status_code == 200
    r = client.delete(f"/api/v1/db/providers/{a['id']}")
    assert r.status_code == 409
    # 复位，避免影响其它用例
    client.delete("/api/v1/config/ai.provider")


def test_config_list_includes_provider_options(client):
    items = client.get("/api/v1/config").json()
    keys = {i["key"] for i in items}
    assert {"ai.provider", "ai.model", "ai.api_key", "ai.base_url"} <= keys
    prov = next(i for i in items if i["key"] == "ai.provider")
    assert isinstance(prov["options"], list)
    # 新建服务商后，选项应包含它
    client.post("/api/v1/db/providers", json=_payload(name="zzz"))
    items2 = client.get("/api/v1/config").json()
    prov2 = next(i for i in items2 if i["key"] == "ai.provider")
    assert "zzz" in prov2["options"]


def test_config_set_ai_model_then_health(client):
    client.post("/api/v1/db/providers", json=_payload(name="a", models=["a1", "a2"]))
    client.put("/api/v1/config/ai.provider", json={"value": "a"})
    client.put("/api/v1/config/ai.model", json={"value": "a2"})

    health = client.get("/health").json()["provider"]
    assert health["name"] == "a"
    assert health["model"] == "a2"
    assert health["source"] == "database"
    assert health["api_key_set"] is True

    # 删除 ai.model 覆盖后回落默认首项
    client.delete("/api/v1/config/ai.model")
    health2 = client.get("/health").json()["provider"]
    assert health2["model"] == "a1"


def test_check_models(client, monkeypatch):
    async def fake(base_url, api_key, models):
        return {
            "ok": True,
            "strategy": "list",
            "available": ["m1"],
            "results": [{"model": m, "ok": m in ("m1",), "message": "可用"} for m in models],
        }

    monkeypatch.setattr("app.services.provider_probe.check_models", fake)
    r = client.post(
        "/api/v1/db/providers/check-models",
        json={"base_url": "https://x/v1", "api_key": "", "models": ["m1", "m2"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["strategy"] == "list"
    assert body["results"][0]["ok"] is True
    assert body["results"][1]["ok"] is False
