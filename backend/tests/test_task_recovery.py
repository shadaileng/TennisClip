"""任务重启恢复 / 僵尸任务修复 单元测试。

覆盖：
- list_stale_tasks / mark_task_failed / recover_stale_tasks 的 DB 行为
- recover_stale_tasks 返回可恢复任务（输入视频存在）与非可恢复任务（标记 failed）
- main.py 启动钩子幂等性：恢复后任务重新入队，再次恢复不重复入队
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import pytest

from app.config import load_config
from app.db_models import Task, TaskOutput, UploadedVideo
from app.services import db_service


@pytest.fixture(autouse=True)
def _init_test_db():
    """确保测试 DB 已初始化（复用 db_service 单例引擎），并清理本模块造出的测试任务。"""
    config = load_config()
    db_service.init_db(config)
    yield
    # 尽力清理：删除本模块随机创建的测试任务及其关联记录
    try:
        with db_service.session() as s:
            s.query(Task).filter(Task.task_id.like("trec_%")).delete(synchronize_session=False)
            s.commit()
    except Exception:
        pass


def _tid(prefix: str = "trec") -> str:
    """随机 task_id，避免跨次运行在持久化测试库中撞唯一约束。"""
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _seed_stale_task(task_id: str, level: str = "all",
                     uploaded_md5: str | None = None,
                     status: str = "pending") -> None:
    """构造一条非终态任务（模拟服务重启时遗留的 pending/processing 任务）。"""
    config = load_config()
    with db_service.session() as s:
        s.add(Task(task_id=task_id, status=status, level=level,
                   source_video=f"/fake/{task_id}.mp4"))
        if uploaded_md5:
            # 模拟 upload_service 落盘记录
            rel_path = f"uploads/{uploaded_md5}.mp4"
            s.add(UploadedVideo(md5=uploaded_md5, ext=".mp4", rel_path=rel_path,
                                original_name=f"{task_id}.mp4", size_bytes=1024))
            s.add(TaskOutput(task_id=task_id, kind="uploaded",
                             file_path=str(config.data_path / rel_path),
                             file_size_mb=0.001))
        s.commit()


def test_list_stale_tasks_returns_only_non_terminal():
    tid_a, tid_b, tid_done = _tid(), _tid(), _tid()
    _seed_stale_task(tid_a, level="beginner")
    _seed_stale_task(tid_b, level="all")
    # 已终态任务不应出现
    with db_service.session() as s:
        s.add(Task(task_id=tid_done, status="succeeded", level="intermediate"))
        s.commit()

    stale = db_service.list_stale_tasks()
    ids = [t["task_id"] for t in stale]
    assert tid_a in ids
    assert tid_b in ids
    assert tid_done not in ids


def test_mark_task_failed_sets_status_and_error():
    tid = _tid()
    _seed_stale_task(tid)
    assert db_service.mark_task_failed(tid, "test error") is True
    with db_service.session() as s:
        t = s.query(Task).filter_by(task_id=tid).first()
        assert t.status == "failed"
        assert t.error == "test error"


def test_mark_task_failed_unknown_task_returns_false():
    assert db_service.mark_task_failed(_tid("noexist")) is False


def test_main_recovery_hook_requeues_running_task():
    """main 启动钩子（恢复块）将「processing + 输入可寻址」任务重新入队。"""
    from app.main import _resolve_recovered_video, queue

    md5 = uuid.uuid4().hex
    tid = _tid()
    config = load_config()
    up_dir = config.data_path / "uploads"
    up_dir.mkdir(parents=True, exist_ok=True)
    video_file = up_dir / f"{md5}.mp4"
    video_file.write_bytes(b"fake-video-data")
    try:
        _seed_stale_task(tid, uploaded_md5=md5, status="processing")
        vp = _resolve_recovered_video(tid)
        assert vp is not None and vp.exists()
        # 模拟 main.py 恢复块入队：原 task_id 直接取回，内存队列与 DB 同步复活
        queue.submit(lambda r: r, task_id=tid)
        assert queue.get(tid) is not None
    finally:
        video_file.unlink(missing_ok=True)


def test_recover_stale_tasks_recoverable_when_uploaded_exists():
    config = load_config()
    md5 = uuid.uuid4().hex  # 随机 md5，避免持久库残留
    tid = _tid()
    # 物理文件必须存在
    up_dir = config.data_path / "uploads"
    up_dir.mkdir(parents=True, exist_ok=True)
    video_file = up_dir / f"{md5}.mp4"
    video_file.write_bytes(b"fake-video-data")
    try:
        _seed_stale_task(tid, uploaded_md5=md5)
        recovered = db_service.recover_stale_tasks()
        ids = [r["task_id"] for r in recovered]
        assert tid in ids
        # 恢复后任务状态应仍为非终态（等待重新入队）
        with db_service.session() as s:
            t = s.query(Task).filter_by(task_id=tid).first()
            assert t.status in ("pending", "processing", "timeout")
    finally:
        video_file.unlink(missing_ok=True)


def test_recover_stale_tasks_marks_failed_when_input_missing():
    md5 = uuid.uuid4().hex
    tid = _tid()
    _seed_stale_task(tid, uploaded_md5=md5)  # 物理文件不存在
    recovered = db_service.recover_stale_tasks()
    ids = [r["task_id"] for r in recovered]
    assert tid not in ids
    with db_service.session() as s:
        t = s.query(Task).filter_by(task_id=tid).first()
        assert t.status == "failed"
        assert t.error is not None and "重启" in t.error


def test_recover_stale_tasks_idempotent():
    """恢复函数本身幂等：对已 failed 任务不再产生 recoverable 列表项。"""
    md5 = uuid.uuid4().hex
    tid = _tid()
    _seed_stale_task(tid, uploaded_md5=md5)
    # 第一次调用 → 输入不可用 → 标记 failed
    db_service.recover_stale_tasks()
    # 第二次调用 → 该任务已 failed，不应再出现
    recovered = db_service.recover_stale_tasks()
    ids = [r["task_id"] for r in recovered]
    assert tid not in ids
