"""Step 5 · 持久化与迁移 — TC-38~TC-43。

测试 workflows 表 CRUD、内置工作流保护、激活机制、graph_json 往返、种子初始化。
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import inspect

import app.workflow.nodes  # noqa: F401 — 触发注册


# ──────────── helpers ────────────


def _make_default_graph_dict():
    """构造一个简单的合法工作流图 dict。"""
    return {
        "version": 1,
        "name": "测试工作流",
        "nodes": [
            {"id": "n1", "type": "input.video", "params": {}},
            {"id": "n2", "type": "edit.concat", "params": {"target_duration": 20}},
            {"id": "n3", "type": "output.artifact", "params": {}},
        ],
        "edges": [
            {"id": "e1", "from": ["n1", "video"], "to": ["n2", "video"]},
            {"id": "e2", "from": ["n2", "video"], "to": ["n3", "video"]},
        ],
    }


def _setup_test_db(tmp_path, monkeypatch):
    """建表并把 db_service 引擎指向测试库，返回 SessionLocal。"""
    from sqlalchemy.orm import sessionmaker

    from app.db import get_engine, init_db
    from app.db_models import Base, Workflows
    from app.services import db_service
    from app.config import load_config

    cfg = load_config()
    test_engine = get_engine(cfg)
    init_db(test_engine)
    # 清理旧数据
    SessionLocal = sessionmaker(bind=test_engine, expire_on_commit=False)
    with SessionLocal() as s:
        s.query(Workflows).delete()
        s.commit()
    monkeypatch.setattr(db_service, "_SessionLocal", SessionLocal)
    return SessionLocal


# ──────────── TC-38 ────────────
class TestTC38MigrationWorkflowTable:
    """迁移后 workflows 表存在；旧库 create_all 兜底仍可启动。"""

    def test_workflows_table_exists(self, tmp_path, monkeypatch):
        """workflows 表已创建（create_all 或 Alembic 迁移）。"""
        from app.db_models import Base

        # 检查 Workflows 表是否在 ORM 元数据中
        table_names = list(Base.metadata.tables.keys())
        assert "workflows" in table_names, f"workflows 表不在 ORM 元数据中: {table_names}"

    def test_workflows_columns(self, tmp_path, monkeypatch):
        """workflows 表包含必要列。"""
        from app.db_models import Workflows

        mapper = inspect(Workflows)
        col_names = {c.key for c in mapper.column_attrs}
        assert "id" in col_names
        assert "name" in col_names
        assert "graph_json" in col_names
        assert "is_builtin" in col_names
        assert "enabled" in col_names


# ──────────── TC-39 ────────────
class TestTC39WorkflowCRUD:
    """CRUD：新增成功；同名冲突抛 WorkflowConflict；更新生效；删除成功。"""

    def test_create_workflow(self, tmp_path, monkeypatch):
        SessionLocal = _setup_test_db(tmp_path, monkeypatch)
        from app.services import workflow_service

        with SessionLocal() as s:
            wf = workflow_service.create_workflow(s, "我的工作流", _make_default_graph_dict())
            s.commit()
            assert wf.id > 0
            assert wf.name == "我的工作流"

    def test_create_duplicate_name_raises(self, tmp_path, monkeypatch):
        SessionLocal = _setup_test_db(tmp_path, monkeypatch)
        from app.services import workflow_service

        with SessionLocal() as s:
            workflow_service.create_workflow(s, "同名流", _make_default_graph_dict())
            s.commit()
        with SessionLocal() as s:
            with pytest.raises(workflow_service.WorkflowConflict):
                workflow_service.create_workflow(s, "同名流", _make_default_graph_dict())
                s.commit()

    def test_update_workflow(self, tmp_path, monkeypatch):
        SessionLocal = _setup_test_db(tmp_path, monkeypatch)
        from app.services import workflow_service

        with SessionLocal() as s:
            wf = workflow_service.create_workflow(s, "待更新", _make_default_graph_dict())
            s.commit()
            wf_id = wf.id

        with SessionLocal() as s:
            updated = workflow_service.update_workflow(s, wf_id, name="已更新")
            s.commit()
            assert updated.name == "已更新"

    def test_delete_workflow(self, tmp_path, monkeypatch):
        SessionLocal = _setup_test_db(tmp_path, monkeypatch)
        from app.services import workflow_service

        with SessionLocal() as s:
            wf = workflow_service.create_workflow(s, "待删除", _make_default_graph_dict())
            s.commit()
            wf_id = wf.id

        with SessionLocal() as s:
            workflow_service.delete_workflow(s, wf_id)
            s.commit()

        with SessionLocal() as s:
            assert workflow_service.get_workflow(s, wf_id) is None


# ──────────── TC-40 ────────────
class TestTC40BuiltinProtection:
    """内置工作流（is_builtin=1）不可删除/修改，返回明确错误。"""

    def test_delete_builtin_raises(self, tmp_path, monkeypatch):
        SessionLocal = _setup_test_db(tmp_path, monkeypatch)
        from app.services import workflow_service

        with SessionLocal() as s:
            wf = workflow_service.create_workflow(
                s, "内置工作流", _make_default_graph_dict(), is_builtin=True
            )
            s.commit()
            wf_id = wf.id

        with SessionLocal() as s:
            with pytest.raises(workflow_service.WorkflowBuiltinProtected):
                workflow_service.delete_workflow(s, wf_id)

    def test_update_builtin_raises(self, tmp_path, monkeypatch):
        SessionLocal = _setup_test_db(tmp_path, monkeypatch)
        from app.services import workflow_service

        with SessionLocal() as s:
            wf = workflow_service.create_workflow(
                s, "内置工作流", _make_default_graph_dict(), is_builtin=True
            )
            s.commit()
            wf_id = wf.id

        with SessionLocal() as s:
            with pytest.raises(workflow_service.WorkflowBuiltinProtected):
                workflow_service.update_workflow(s, wf_id, name="试图修改")


# ──────────── TC-41 ────────────
class TestTC41ActivateWorkflow:
    """activate 写入 system_config.pipeline.workflow_id，重新读取命中该工作流。"""

    def test_activate_and_read_back(self, tmp_path, monkeypatch):
        SessionLocal = _setup_test_db(tmp_path, monkeypatch)
        from app.services import workflow_service, config_service
        from app.config import load_config

        cfg = load_config()

        with SessionLocal() as s:
            wf = workflow_service.create_workflow(s, "激活测试", _make_default_graph_dict())
            s.commit()
            wf_id = wf.id

        with SessionLocal() as s:
            workflow_service.activate_workflow(s, wf_id)
            s.commit()

        with SessionLocal() as s:
            wf_cfg = config_service.get_config_value(s, "workflow.default_graph_id", cfg)
            assert wf_cfg == str(wf_id)

    def test_activate_nonexistent_raises(self, tmp_path, monkeypatch):
        SessionLocal = _setup_test_db(tmp_path, monkeypatch)
        from app.services import workflow_service

        with SessionLocal() as s:
            with pytest.raises(ValueError, match="不存在"):
                workflow_service.activate_workflow(s, 99999)


# ──────────── TC-42 ────────────
class TestTC42GraphJsonRoundtrip:
    """graph_json 往返无损（含中文 name、嵌套 params、禁用节点）。"""

    def test_roundtrip_preserves_all_fields(self, tmp_path, monkeypatch):
        SessionLocal = _setup_test_db(tmp_path, monkeypatch)
        from app.services import workflow_service

        original = _make_default_graph_dict()
        # 添加禁用节点和中文
        original["name"] = "往返测试含中文"
        original["nodes"][0]["enabled"] = True
        original["nodes"][1]["enabled"] = False

        with SessionLocal() as s:
            wf = workflow_service.create_workflow(s, "往返", original)
            s.commit()
            wf_id = wf.id

        with SessionLocal() as s:
            loaded = workflow_service.get_workflow(s, wf_id)
            loaded_graph = json.loads(loaded.graph_json)
            assert loaded_graph == original

    def test_roundtrip_chinese_name(self, tmp_path, monkeypatch):
        SessionLocal = _setup_test_db(tmp_path, monkeypatch)
        from app.services import workflow_service

        original = _make_default_graph_dict()
        original["name"] = "中文工作流名称"

        with SessionLocal() as s:
            wf = workflow_service.create_workflow(s, "中文", original)
            s.commit()
            wf_id = wf.id

        with SessionLocal() as s:
            loaded = workflow_service.get_workflow(s, wf_id)
            loaded_graph = json.loads(loaded.graph_json)
            assert loaded_graph["name"] == "中文工作流名称"


# ──────────── TC-43 ────────────
class TestTC43SeedDefaultWorkflow:
    """首次启动种子：无任何 workflow 时按简易模式三 KV 生成默认内置工作流。"""

    def test_seed_creates_builtin(self, tmp_path, monkeypatch):
        SessionLocal = _setup_test_db(tmp_path, monkeypatch)
        from app.services import workflow_service
        from app.config import load_config

        cfg = load_config()

        with SessionLocal() as s:
            workflow_service.seed_default_workflow(s, cfg)
            s.commit()

        with SessionLocal() as s:
            wfs = workflow_service.list_workflows(s)
            builtins = [w for w in wfs if w.is_builtin]
            assert len(builtins) >= 1
            assert builtins[0].name == "默认工作流"

    def test_seed_idempotent(self, tmp_path, monkeypatch):
        """多次调用 seed 不会重复创建。"""
        SessionLocal = _setup_test_db(tmp_path, monkeypatch)
        from app.services import workflow_service
        from app.config import load_config

        cfg = load_config()

        with SessionLocal() as s:
            workflow_service.seed_default_workflow(s, cfg)
            s.commit()
        with SessionLocal() as s:
            workflow_service.seed_default_workflow(s, cfg)
            s.commit()

        with SessionLocal() as s:
            wfs = workflow_service.list_workflows(s)
            builtins = [w for w in wfs if w.is_builtin and w.name == "默认工作流"]
            assert len(builtins) == 1
