"""工作流持久化服务：CRUD、激活、种子。

- create_workflow / get_workflow / list_workflows / update_workflow / delete_workflow
- clone_workflow（复制为可编辑副本，内置也可复制）
- activate_workflow / seed_default_workflow / seed_cv_workflow / seed_cv_debug_workflow / seed_builtin_presets
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


def _cv_enhanced_graph_dict() -> dict:
    """「CV 增强工作流」内置预置图（方案 12 · 5.2，示例 C 降本链路）。

    input → preprocess → detect.tracknet → post.score_highlights
        ├→ edit.concat ────────┐
        └→ report.technical ───┴→ output.artifact

    信号候选定位（TrackNet 球追踪）与高光初筛（CLIP 零样本评分）由 CV 层承担，
    LLM 仅保留报告生成（analyze.highlight 不入图，这是「降本」语义的来源）。
    detect.* 为 on_failure=skip 级联锚点：缺 CV 依赖/权重时跳过、任务不失败。
    """
    return {
        "version": 1,
        "name": "CV 增强工作流",
        "nodes": [
            {"id": "n1", "type": "input.video", "params": {}, "enabled": True},
            {"id": "n2", "type": "preprocess.transcode", "params": {}, "enabled": True},
            {"id": "n3", "type": "detect.tracknet", "params": {}, "enabled": True},
            {"id": "n4", "type": "post.score_highlights", "params": {}, "enabled": True},
            {"id": "n5", "type": "edit.concat", "params": {}, "enabled": True},
            {"id": "n6", "type": "report.technical", "params": {}, "enabled": True},
            {"id": "n7", "type": "output.artifact", "params": {}, "enabled": True},
        ],
        "edges": [
            {"id": "e1", "from": ["n1", "video"], "to": ["n2", "video"]},
            {"id": "e2", "from": ["n2", "video"], "to": ["n3", "video"]},
            {"id": "e3", "from": ["n2", "duration"], "to": ["n3", "duration"]},
            {"id": "e4", "from": ["n3", "candidates"], "to": ["n4", "candidates"]},
            {"id": "e5", "from": ["n2", "video"], "to": ["n4", "video"]},
            {"id": "e6", "from": ["n4", "highlight"], "to": ["n5", "highlight"]},
            {"id": "e7", "from": ["n2", "video"], "to": ["n5", "video"]},
            {"id": "e8", "from": ["n4", "highlight"], "to": ["n6", "highlight"]},
            {"id": "e9", "from": ["n2", "video"], "to": ["n6", "video"]},
            {"id": "e10", "from": ["n5", "video"], "to": ["n7", "video"]},
            {"id": "e11", "from": ["n5", "highlight"], "to": ["n7", "highlight"]},
            {"id": "e12", "from": ["n6", "report"], "to": ["n7", "report"]},
        ],
    }


def seed_cv_workflow(db: Session) -> None:
    """「CV 增强工作流」内置预设种子（方案 12 · 5.2，按名幂等）。

    与 seed_default_workflow 不同：不设全表 count 门控——只要名称不存在即补种，
    兼容存量库已有用户数据的场景。
    """
    name = "CV 增强工作流"
    if db.query(Workflows).filter_by(name=name).first():
        return

    wf = Workflows(
        name=name,
        description="方案 12 示例 C 降本链路：TrackNet 球追踪候选 + CLIP 零样本初筛产出高光，"
                    "LLM 仅出报告；需 CV 依赖组与权重（uv sync --extra cv，缺依赖时 CV 层级联跳过）",
        graph_json=json.dumps(_cv_enhanced_graph_dict(), ensure_ascii=False),
        is_builtin=1,
        enabled=1,
        sort_order=1,
    )
    db.add(wf)
    db.flush()
    logger.info("workflow: seeded CV builtin id={}", wf.id)


def _cv_debug_graph_dict() -> dict:
    """「CV 增强调试工作流」内置预置图：示例 C 链路 + 轨迹可视化诊断分支。

    在 _cv_enhanced_graph_dict 基础上叠加（诊断分支不影响主链路）::

        n2.video / n3.track / n2.duration → n8 post.visualize_track
            （产物 track_overlay.mp4 经 persists 落库）

    n8 为终端节点（输出端口悬空合法：R9 仅要求有入边）、on_failure=skip——
    缺 CV 依赖/权重时仅诊断分支自身跳过，集锦/报告产物不受影响。
    """
    d = _cv_enhanced_graph_dict()
    d["name"] = "CV 增强调试工作流"
    d["nodes"].append(
        {"id": "n8", "type": "post.visualize_track", "params": {}, "enabled": True}
    )
    d["edges"].extend([
        {"id": "e13", "from": ["n2", "video"], "to": ["n8", "video"]},
        {"id": "e14", "from": ["n3", "track"], "to": ["n8", "track"]},
        {"id": "e15", "from": ["n2", "duration"], "to": ["n8", "duration"]},
    ])
    return d


def seed_cv_debug_workflow(db: Session) -> None:
    """「CV 增强调试工作流」内置预设种子（按名缺则补，存量库也生效）。

    诊断版不改动既有「CV 增强工作流」（内置图 API 层 403 不可修改），
    常规任务继续用原图零开销；需要核对 TrackNet 检出时激活本预设。
    """
    name = "CV 增强调试工作流"
    if db.query(Workflows).filter_by(name=name).first():
        return

    wf = Workflows(
        name=name,
        description="诊断版：CV 增强链路 + 轨迹可视化分支（球轨迹折线 / 球员框 / 检出时间线 "
                    "→ track_overlay.mp4），用于人工核对 TrackNet 是否检出球；需 CV 依赖组与权重",
        graph_json=json.dumps(_cv_debug_graph_dict(), ensure_ascii=False),
        is_builtin=1,
        enabled=1,
        sort_order=2,
    )
    db.add(wf)
    db.flush()
    logger.info("workflow: seeded CV-debug builtin id={}", wf.id)


def clone_workflow(db: Session, wf_id: int) -> Workflows:
    """复制工作流为可编辑副本（内置也可复制——绕开内置图 403 限制的正式入口）。

    副本 ``is_builtin=0``、可改可删；名称自动生成「{原名} 副本」，
    冲突时追加序号（副本 2、副本 3…）；graph_json 内嵌 name 同步为副本名。
    不存在抛 ValueError。
    """
    wf = db.query(Workflows).filter_by(id=wf_id).first()
    if wf is None:
        raise ValueError(f"工作流不存在：id={wf_id}")

    base = f"{wf.name} 副本"
    name = base
    seq = 2
    while db.query(Workflows).filter_by(name=name).first():
        name = f"{base} {seq}"
        seq += 1

    graph_dict = json.loads(wf.graph_json)
    graph_dict["name"] = name

    clone = Workflows(
        name=name,
        description=wf.description,
        graph_json=json.dumps(graph_dict, ensure_ascii=False),
        is_builtin=0,
        enabled=1,
        sort_order=wf.sort_order,
    )
    db.add(clone)
    db.flush()
    logger.info("workflow: cloned id={} → id={} name={}", wf_id, clone.id, name)
    return clone


def seed_builtin_presets(db: Session, config) -> None:
    """启动期内置预设种子（幂等）：默认工作流 + CV 增强工作流 + CV 增强调试工作流。

    - 默认工作流：全表 count==0（首次启动）才生成，语义不变；
    - CV 增强工作流 / CV 增强调试工作流：按名缺则补（存量库也生效）。

    由 main.py 启动时调用（try/except 包裹，失败仅告警不阻断）。
    """
    seed_default_workflow(db, config)
    seed_cv_workflow(db)
    seed_cv_debug_workflow(db)
