"""工作流持久化服务：CRUD、激活、种子。

- create_workflow / get_workflow / list_workflows / update_workflow / delete_workflow
- activate_workflow / seed_default_workflow
- WorkflowConflict / WorkflowBuiltinProtected 异常
"""

from __future__ import annotations

import json
from typing import Optional

from sqlalchemy.orm import Session

from app.db_models import SystemConfig, Workflows
from app.utils.logger import get_logger

logger = get_logger(__name__)


class WorkflowConflict(Exception):
    """工作流名称冲突。"""


class WorkflowBuiltinProtected(Exception):
    """内置工作流不可修改/删除。"""


def create_workflow(
    db: Session,
    name: str,
    graph_dict: dict,
    description: str = "",
    is_builtin: bool = False,
) -> Workflows:
    """新建工作流。同名冲突抛 WorkflowConflict。"""
    existing = db.query(Workflows).filter_by(name=name).first()
    if existing:
        raise WorkflowConflict(f"工作流名称已存在：{name}")

    wf = Workflows(
        name=name,
        description=description,
        graph_json=json.dumps(graph_dict, ensure_ascii=False),
        is_builtin=1 if is_builtin else 0,
        enabled=1,
        sort_order=0,
    )
    db.add(wf)
    db.flush()
    logger.info("workflow: created id={} name={} builtin={}", wf.id, wf.name, wf.is_builtin)
    return wf


def get_workflow(db: Session, wf_id: int) -> Optional[Workflows]:
    """按 id 获取工作流；不存在返回 None。"""
    return db.query(Workflows).filter_by(id=wf_id).first()


def list_workflows(db: Session, enabled_only: bool = False) -> list[Workflows]:
    """列出所有工作流（按 sort_order + id 升序）。"""
    q = db.query(Workflows)
    if enabled_only:
        q = q.filter_by(enabled=1)
    return q.order_by(Workflows.sort_order, Workflows.id).all()


def update_workflow(
    db: Session,
    wf_id: int,
    name: Optional[str] = None,
    graph_dict: Optional[dict] = None,
    description: Optional[str] = None,
    enabled: Optional[int] = None,
    sort_order: Optional[int] = None,
) -> Workflows:
    """更新工作流。内置工作流抛 WorkflowBuiltinProtected；同名冲突抛 WorkflowConflict。"""
    wf = db.query(Workflows).filter_by(id=wf_id).first()
    if wf is None:
        raise ValueError(f"工作流不存在：id={wf_id}")
    if wf.is_builtin:
        raise WorkflowBuiltinProtected(f"内置工作流不可修改：{wf.name}")

    if name is not None:
        existing = db.query(Workflows).filter(Workflows.name == name, Workflows.id != wf_id).first()
        if existing:
            raise WorkflowConflict(f"工作流名称已存在：{name}")
        wf.name = name

    if graph_dict is not None:
        wf.graph_json = json.dumps(graph_dict, ensure_ascii=False)
    if description is not None:
        wf.description = description
    if enabled is not None:
        wf.enabled = enabled
    if sort_order is not None:
        wf.sort_order = sort_order

    db.flush()
    logger.info("workflow: updated id={} name={}", wf.id, wf.name)
    return wf


def delete_workflow(db: Session, wf_id: int) -> None:
    """删除工作流。内置工作流抛 WorkflowBuiltinProtected。"""
    wf = db.query(Workflows).filter_by(id=wf_id).first()
    if wf is None:
        raise ValueError(f"工作流不存在：id={wf_id}")
    if wf.is_builtin:
        raise WorkflowBuiltinProtected(f"内置工作流不可删除：{wf.name}")

    db.delete(wf)
    db.flush()
    logger.info("workflow: deleted id={} name={}", wf_id, wf.name)


def activate_workflow(db: Session, wf_id: int) -> Workflows:
    """激活工作流：写入 system_config.pipeline.workflow_id。"""
    wf = db.query(Workflows).filter_by(id=wf_id).first()
    if wf is None:
        raise ValueError(f"工作流不存在：id={wf_id}")

    key = "workflow.default_graph_id"
    row = db.query(SystemConfig).filter_by(key=key).first()
    if row:
        row.value = str(wf_id)
    else:
        db.add(SystemConfig(key=key, value=str(wf_id)))

    db.flush()
    logger.info("workflow: activated id={} name={}", wf.id, wf.name)
    return wf


def seed_default_workflow(db: Session, config) -> None:
    """首次启动种子：无任何 workflow 时生成默认内置工作流。

    默认图由 compile_from_legacy() 编译（沿用简易模式三 KV）。
    """
    from app.workflow.presets import compile_from_legacy

    count = db.query(Workflows).count()
    if count > 0:
        return

    graph = compile_from_legacy()
    graph_dict = graph.to_dict()

    wf = Workflows(
        name="默认工作流",
        description="由简易模式配置编译的默认工作流（预处理→高光识别→剪辑→报告）",
        graph_json=json.dumps(graph_dict, ensure_ascii=False),
        is_builtin=1,
        enabled=1,
        sort_order=0,
    )
    db.add(wf)
    db.flush()
    logger.info("workflow: seeded default builtin id={}", wf.id)
