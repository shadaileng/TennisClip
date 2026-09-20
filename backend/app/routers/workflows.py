"""工作流路由：CRUD、Schema、激活、校验。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import db_service
from app.services import workflow_service
from app.utils.logger import get_logger
from app.workflow.spec import build_schema
from app.workflow.graph import WorkflowGraph
import app.workflow.nodes  # noqa: F401 — 触发所有内置节点注册

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])


# ──────── 请求/响应模型 ────────


class WorkflowCreate(BaseModel):
    name: str
    graph: dict
    description: str = ""
    is_builtin: bool = False


class WorkflowUpdate(BaseModel):
    name: Optional[str] = None
    graph: Optional[dict] = None
    description: Optional[str] = None
    enabled: Optional[int] = None
    sort_order: Optional[int] = None


class ValidateRequest(BaseModel):
    graph: dict


# ──────── 路由 ────────


@router.get("/schema")
def get_schema():
    """返回全部节点的 Schema（前端渲染依据）。"""
    return build_schema()


@router.get("")
def list_workflows():
    """列出所有工作流，含 is_active 标记。"""
    with db_service.session() as s:
        wfs = workflow_service.list_workflows(s)
        # 读取激活 ID
        from app.config import load_config
        from app.services.config_service import get_config_value
        cfg = load_config()
        active_id_str = get_config_value(s, "workflow.default_graph_id", cfg)
        try:
            active_id = int(active_id_str) if active_id_str else None
        except (ValueError, TypeError):
            active_id = None

    return [
        {
            "id": wf.id,
            "name": wf.name,
            "description": wf.description,
            "is_builtin": wf.is_builtin,
            "enabled": wf.enabled,
            "sort_order": wf.sort_order,
            "is_active": wf.id == active_id,
            "created_at": wf.created_at.isoformat() if wf.created_at else None,
            "updated_at": wf.updated_at.isoformat() if wf.updated_at else None,
        }
        for wf in wfs
    ]


@router.get("/{wf_id}")
def get_workflow(wf_id: int):
    """获取单个工作流详情。"""
    with db_service.session() as s:
        wf = workflow_service.get_workflow(s, wf_id)
        if wf is None:
            raise HTTPException(status_code=404, detail=f"工作流不存在：id={wf_id}")
        graph_dict = __import__("json").loads(wf.graph_json)
    return {
        "id": wf.id,
        "name": wf.name,
        "description": wf.description,
        "graph": graph_dict,
        "is_builtin": wf.is_builtin,
        "enabled": wf.enabled,
        "sort_order": wf.sort_order,
    }


@router.post("")
def create_workflow(body: WorkflowCreate):
    """新建工作流。图非法 400；重名 409。"""
    # 校验图
    graph = WorkflowGraph.from_dict(body.graph)
    result = graph.validate()
    if not result["ok"]:
        error_msgs = "; ".join(e["message"] for e in result["errors"])
        raise HTTPException(status_code=400, detail=f"图校验失败：{error_msgs}")

    try:
        with db_service.session() as s:
            wf = workflow_service.create_workflow(
                s, body.name, body.graph, description=body.description,
                is_builtin=body.is_builtin,
            )
            s.commit()
            wf_id = wf.id
    except workflow_service.WorkflowConflict as e:
        raise HTTPException(status_code=409, detail=str(e))

    return {"id": wf_id, "name": body.name}


@router.put("/{wf_id}")
def update_workflow(wf_id: int, body: WorkflowUpdate):
    """更新工作流。内置 403；图非法 400。"""
    if body.graph is not None:
        graph = WorkflowGraph.from_dict(body.graph)
        result = graph.validate()
        if not result["ok"]:
            error_msgs = "; ".join(e["message"] for e in result["errors"])
            raise HTTPException(status_code=400, detail=f"图校验失败：{error_msgs}")

    try:
        with db_service.session() as s:
            wf = workflow_service.update_workflow(
                s, wf_id, name=body.name, graph_dict=body.graph,
                description=body.description, enabled=body.enabled,
                sort_order=body.sort_order,
            )
            s.commit()
    except workflow_service.WorkflowBuiltinProtected as e:
        raise HTTPException(status_code=403, detail=str(e))
    except workflow_service.WorkflowConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return {"id": wf.id, "name": wf.name}


@router.delete("/{wf_id}")
def delete_workflow(wf_id: int):
    """删除工作流。内置/激活中 409。"""
    try:
        with db_service.session() as s:
            workflow_service.delete_workflow(s, wf_id)
            s.commit()
    except workflow_service.WorkflowBuiltinProtected as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return {"ok": True}


@router.post("/{wf_id}/activate")
def activate_workflow(wf_id: int):
    """激活工作流。"""
    try:
        with db_service.session() as s:
            workflow_service.activate_workflow(s, wf_id)
            s.commit()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return {"ok": True, "activated_id": wf_id}


@router.post("/validate")
def validate_workflow(body: ValidateRequest):
    """校验草稿图（不落库），返回 {ok, errors}。"""
    graph = WorkflowGraph.from_dict(body.graph)
    result = graph.validate()
    return result
