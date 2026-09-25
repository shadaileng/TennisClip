"""任务重试（retry）单元测试。

覆盖：POST /api/v1/tasks/{id}/retry 的校验分支（404/409/400/成功），
以及 get_task_detail 返回 level 字段。
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import app.db_models  # noqa: F401
from app.db_models import Base, Task, TaskInput, TaskOutput, TaskResult, UploadedVideo


def _make_engine(tmp_path):
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{tmp_path / 'data' / 'retry.db'}"
    engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def captured_retry_ids():
    """收集重试产生的新 task_id，供 fixture 清理（新任务为随机 uuid，无法预知）。"""
    return []


@pytest.fixture
def client(tmp_path, monkeypatch, captured_retry_ids):
    """临时 SQLite 隔离测试库；复用真实数据目录（main.app 的 config 不变，避免 property 赋值）。

    输入文件以随机 32 位 hex 命名落真实 data/uploads/，测试后清理；
    仅 HTTP 分支校验（404/409/400/200），新任务入真实队列但 level=all 且
    真实工作流在 4 核下耗时过长——测试不等待其完成（TestClient 请求返回即断言）。
    """
    import uuid
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'bootstrap.db'}")
    import app.main
    from app.services import db_service

    engine = _make_engine(tmp_path)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(db_service, "_engine", engine)
    monkeypatch.setattr(db_service, "_SessionLocal", SessionLocal)

    # 输入文件落真实数据目录（exists_physically 依赖 config.data_path / rel_path）
    import app.config as _cfgmod
    real_data = _cfgmod.load_config().data_path
    md5 = uuid.uuid4().hex[:32]
    video = real_data / "uploads" / f"{md5}.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"fake video")
    rel_path = f"uploads/{md5}.mp4"

    cleanup_files = [video]
    cleanup_task_ids = ["origtask0001", "runningtask01", "missinginput01"]
    try:
        with db_service.session() as s:
            s.add(UploadedVideo(
                md5=md5, ext=".mp4", size_bytes=10, original_name="v.mp4",
                rel_path=rel_path,
            ))
            s.add(Task(
                task_id="origtask0001", status="failed", level="all",
                source_video="v.mp4", error="任务已被用户停止（origtask0001）",
                created_at=datetime(2026, 9, 25, 1, 0),
            ))
            s.add(TaskOutput(
                task_id="origtask0001", kind="uploaded",
                file_path=str(video), file_size_mb=0.00001,
            ))
            s.add(Task(
                task_id="runningtask01", status="processing", level="all",
                source_video="v.mp4", created_at=datetime(2026, 9, 25, 3, 0),
            ))
            s.add(TaskOutput(
                task_id="runningtask01", kind="uploaded",
                file_path=str(video),
            ))
            s.add(Task(
                task_id="missinginput01", status="failed", level="all",
                source_video="v.mp4", created_at=datetime(2026, 9, 25, 4, 0),
            ))
            s.commit()

        c = TestClient(app.main.app)
        yield c
    finally:
        # 清理测试任务行（含重试入队产生的新 pending 行）与物理文件
        all_task_ids = cleanup_task_ids + [t for t in captured_retry_ids]
        with db_service.session() as s:
            s.query(TaskResult).filter(TaskResult.task_id.in_(all_task_ids)).delete(synchronize_session=False)
            s.query(TaskOutput).filter(TaskOutput.task_id.in_(all_task_ids)).delete(synchronize_session=False)
            s.query(TaskInput).filter(TaskInput.task_id.in_(all_task_ids)).delete(synchronize_session=False)
            s.query(Task).filter(Task.task_id.in_(all_task_ids)).delete(synchronize_session=False)
            s.query(UploadedVideo).filter(UploadedVideo.md5 == md5).delete(synchronize_session=False)
            s.commit()
        for f in cleanup_files:
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass


def test_get_task_detail_includes_level(client):
    """get_task_detail 返回 level 字段（retry 按原 level 提交）。"""
    from app.services import db_service
    detail = db_service.get_task_detail("origtask0001")
    assert detail is not None
    assert detail["level"] == "all"
    assert detail["status"] == "failed"


def test_retry_success_returns_new_task(client):
    """终态任务 + 输入可用：重试成功，返回新 task_id（秒传复用，不入真实队列验证）。"""
    res = client.post("/api/v1/tasks/origtask0001/retry")
    assert res.status_code == 200
    body = res.json()
    assert body["retried_from"] == "origtask0001"
    assert "task_id" in body and body["status"] == "pending"


def test_retry_success_new_task_registered(client, captured_retry_ids):
    """重试后新任务进入真实队列（状态可查，status 为 pending/processing，level 继承原任务）。"""
    res = client.post("/api/v1/tasks/origtask0001/retry")
    assert res.status_code == 200
    new_id = res.json()["task_id"]
    captured_retry_ids.append(new_id)
    # 经 /api/v1/tasks/{id} 查询（内存队列命中；level 存于 DB，TaskResult 无该字段）
    got = client.get(f"/api/v1/tasks/{new_id}")
    assert got.status_code == 200
    body = got.json()
    assert body["task_id"] == new_id
    assert body["status"] in ("pending", "processing")


def test_retry_unknown_task_404(client):
    res = client.post("/api/v1/tasks/nonexistent123/retry")
    assert res.status_code == 404


def test_retry_running_task_409(client):
    """进行中任务不允许重试（409）。"""
    res = client.post("/api/v1/tasks/runningtask01/retry")
    assert res.status_code == 409


def test_retry_missing_input_400(client):
    """输入视频未登记（无 TaskOutput）→ 400。"""
    res = client.post("/api/v1/tasks/missinginput01/retry")
    assert res.status_code == 400
