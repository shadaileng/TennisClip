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


# ──────────── 5.2: 内置预设种子 ────────────
class TestSeedBuiltinPresets:
    """方案 12 · 5.2：CV 增强内置预设种子（按名幂等）+ seed_builtin_presets 包装。"""

    def test_seed_cv_creates_builtin_with_valid_graph(self, tmp_path, monkeypatch):
        """种子创建 is_builtin 的「CV 增强工作流」，图通过 R1~R10 校验。"""
        SessionLocal = _setup_test_db(tmp_path, monkeypatch)
        from app.services import workflow_service
        from app.workflow.graph import WorkflowGraph

        with SessionLocal() as s:
            workflow_service.seed_cv_workflow(s)
            s.commit()

        with SessionLocal() as s:
            wfs = workflow_service.list_workflows(s)
            cv = [w for w in wfs if w.name == "CV 增强工作流"]
            assert len(cv) == 1
            assert cv[0].is_builtin == 1
            graph = json.loads(cv[0].graph_json)
            res = WorkflowGraph.from_dict(graph).validate()
            assert res["ok"], res.get("errors")

    def test_seed_cv_idempotent_by_name(self, tmp_path, monkeypatch):
        """按名幂等：已有其他工作流（count>0）时重复种子不产生重复行。"""
        SessionLocal = _setup_test_db(tmp_path, monkeypatch)
        from app.services import workflow_service

        # 先造一条用户工作流（全表 count>0，隔离默认种子的 count==0 门控）
        with SessionLocal() as s:
            workflow_service.create_workflow(s, "用户工作流", _make_default_graph_dict())
            s.commit()

        with SessionLocal() as s:
            workflow_service.seed_cv_workflow(s)
            s.commit()
        with SessionLocal() as s:
            workflow_service.seed_cv_workflow(s)
            s.commit()

        with SessionLocal() as s:
            wfs = workflow_service.list_workflows(s)
            assert len([w for w in wfs if w.name == "CV 增强工作流"]) == 1

    def test_seed_builtin_presets_seeds_all_idempotent(self, tmp_path, monkeypatch):
        """seed_builtin_presets：空库同时种子默认 / CV 增强 / CV 增强调试三个内置预设，重复调用幂等。"""
        SessionLocal = _setup_test_db(tmp_path, monkeypatch)
        from app.services import workflow_service
        from app.config import load_config

        cfg = load_config()

        with SessionLocal() as s:
            workflow_service.seed_builtin_presets(s, cfg)
            s.commit()
        with SessionLocal() as s:
            workflow_service.seed_builtin_presets(s, cfg)
            s.commit()

        with SessionLocal() as s:
            wfs = workflow_service.list_workflows(s)
            assert len([w for w in wfs if w.name == "默认工作流"]) == 1
            assert len([w for w in wfs if w.name == "CV 增强工作流"]) == 1
            assert len([w for w in wfs if w.name == "CV 增强调试工作流"]) == 1

    def test_seed_presets_respects_existing_rows(self, tmp_path, monkeypatch):
        """已有数据时不补默认工作流（count==0 门控不变），CV 两个预设按名缺则补。"""
        SessionLocal = _setup_test_db(tmp_path, monkeypatch)
        from app.services import workflow_service
        from app.config import load_config

        cfg = load_config()

        with SessionLocal() as s:
            workflow_service.create_workflow(s, "用户工作流", _make_default_graph_dict())
            s.commit()

        with SessionLocal() as s:
            workflow_service.seed_builtin_presets(s, cfg)
            s.commit()

        with SessionLocal() as s:
            wfs = workflow_service.list_workflows(s)
            assert len([w for w in wfs if w.name == "默认工作流"]) == 0
            assert len([w for w in wfs if w.name == "CV 增强工作流"]) == 1
            assert len([w for w in wfs if w.name == "CV 增强调试工作流"]) == 1

    def test_cv_preset_is_builtin_protected(self, tmp_path, monkeypatch):
        """内置保护：CV 预设更新/删除均抛 WorkflowBuiltinProtected。"""
        SessionLocal = _setup_test_db(tmp_path, monkeypatch)
        from app.services import workflow_service
        from app.services.workflow_service import WorkflowBuiltinProtected

        with SessionLocal() as s:
            workflow_service.seed_cv_workflow(s)
            s.commit()
        with SessionLocal() as s:
            cv = [w for w in workflow_service.list_workflows(s)
                  if w.name == "CV 增强工作流"][0]
            cv_id = cv.id

        with SessionLocal() as s:
            with pytest.raises(WorkflowBuiltinProtected):
                workflow_service.update_workflow(s, cv_id, name="改名")
            with pytest.raises(WorkflowBuiltinProtected):
                workflow_service.delete_workflow(s, cv_id)


# ──────────── 调试版预设（诊断分支） ────────────
class TestSeedCvDebugPreset:
    """「CV 增强调试工作流」种子：图合法含可视化分支 + 存量库按名缺则补。"""

    def test_debug_seed_creates_builtin_with_valid_graph(self, tmp_path, monkeypatch):
        """种子创建 is_builtin 调试预设：图过 R1~R10，含 post.visualize_track 诊断分支。"""
        SessionLocal = _setup_test_db(tmp_path, monkeypatch)
        from app.services import workflow_service
        from app.workflow.graph import WorkflowGraph

        with SessionLocal() as s:
            workflow_service.seed_cv_debug_workflow(s)
            s.commit()

        with SessionLocal() as s:
            wfs = workflow_service.list_workflows(s)
            dbg = [w for w in wfs if w.name == "CV 增强调试工作流"]
            assert len(dbg) == 1
            assert dbg[0].is_builtin == 1
            graph = json.loads(dbg[0].graph_json)
            res = WorkflowGraph.from_dict(graph).validate()
            assert res["ok"], res.get("errors")
            # 诊断分支：n2.video / n3.track / n2.duration → n8 post.visualize_track
            types = {n["id"]: n["type"] for n in graph["nodes"]}
            assert types.get("n8") == "post.visualize_track"
            edges = {(e["from"][0], e["from"][1], e["to"][0], e["to"][1])
                     for e in graph["edges"]}
            assert ("n3", "track", "n8", "track") in edges
            assert ("n2", "video", "n8", "video") in edges

    def test_debug_seed_fills_legacy_db(self, tmp_path, monkeypatch):
        """存量库（已有 CV 增强、无调试版）→ seed_builtin_presets 补种调试版，原图不动。"""
        SessionLocal = _setup_test_db(tmp_path, monkeypatch)
        from app.services import workflow_service
        from app.config import load_config

        cfg = load_config()
        # 模拟旧版存量库：只有 CV 增强
        with SessionLocal() as s:
            workflow_service.seed_cv_workflow(s)
            s.commit()
        with SessionLocal() as s:
            workflow_service.seed_builtin_presets(s, cfg)
            s.commit()

        with SessionLocal() as s:
            wfs = workflow_service.list_workflows(s)
            assert len([w for w in wfs if w.name == "CV 增强调试工作流"]) == 1
            # 原「CV 增强工作流」保持不含可视化节点（不改存量内置图）
            cv = [w for w in wfs if w.name == "CV 增强工作流"][0]
            cv_types = {n["type"] for n in json.loads(cv.graph_json)["nodes"]}
            assert "post.visualize_track" not in cv_types


# ──────────── 工作流复制 ────────────
class TestCloneWorkflow:
    """clone_workflow：内置可复制为可编辑副本，名称自动避让，原图/原行不变。"""

    def test_clone_builtin_produces_editable_copy(self, tmp_path, monkeypatch):
        """内置预设 → 副本 is_builtin=0、图一致（仅内嵌 name 同步）、原行不变。"""
        SessionLocal = _setup_test_db(tmp_path, monkeypatch)
        from app.services import workflow_service
        from app.services.workflow_service import WorkflowBuiltinProtected

        with SessionLocal() as s:
            workflow_service.seed_cv_debug_workflow(s)
            s.commit()
        with SessionLocal() as s:
            src = [w for w in workflow_service.list_workflows(s)
                   if w.name == "CV 增强调试工作流"][0]
            src_id, src_graph_json = src.id, src.graph_json
            clone = workflow_service.clone_workflow(s, src_id)
            s.commit()

        with SessionLocal() as s:
            wfs = workflow_service.list_workflows(s)
            copies = [w for w in wfs if w.name == "CV 增强调试工作流 副本"]
            assert len(copies) == 1
            copy = copies[0]
            assert copy.is_builtin == 0
            # 图一致：仅内嵌 name 改为副本名
            src_g = json.loads(src_graph_json)
            copy_g = json.loads(copy.graph_json)
            assert copy_g.pop("name") == copy.name
            src_g.pop("name")
            assert copy_g == src_g
            # 副本可改可删；原内置行受保护且未被改动
            workflow_service.update_workflow(s, copy.id, description="改")
            workflow_service.delete_workflow(s, copy.id)
            with pytest.raises(WorkflowBuiltinProtected):
                workflow_service.update_workflow(s, src_id, name="改名")
            assert [w for w in workflow_service.list_workflows(s)
                    if w.id == src_id][0].graph_json == src_graph_json

    def test_clone_name_conflict_appends_seq(self, tmp_path, monkeypatch):
        """重复复制同名自动避让：副本 → 副本 2。"""
        SessionLocal = _setup_test_db(tmp_path, monkeypatch)
        from app.services import workflow_service

        with SessionLocal() as s:
            wf = workflow_service.create_workflow(s, "我的工作流", _make_default_graph_dict())
            s.commit()
            first_id = wf.id
        with SessionLocal() as s:
            c1 = workflow_service.clone_workflow(s, first_id)
            s.commit()
        with SessionLocal() as s:
            c2 = workflow_service.clone_workflow(s, first_id)
            s.commit()

        assert c1.name == "我的工作流 副本"
        assert c2.name == "我的工作流 副本 2"

    def test_clone_missing_raises(self, tmp_path, monkeypatch):
        """不存在的 id → ValueError（路由映射 404）。"""
        SessionLocal = _setup_test_db(tmp_path, monkeypatch)
        from app.services import workflow_service

        with SessionLocal() as s:
            with pytest.raises(ValueError, match="不存在"):
                workflow_service.clone_workflow(s, 99999)
