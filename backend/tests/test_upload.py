"""两步上传 + MD5 秒传 端到端测试。

覆盖：MD5 预检命中/未命中、分片上传、断点续传（仅传缺失片）、
整文件 MD5 校验失败 409、秒传复用已落盘视频。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import zlib

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(autouse=True, scope="session")
def _isolate_upload_storage():
    """尽量清空上传目录与 uploaded_videos 表（尽力而为，写库失败不阻断测试）。

    各测试另用随机内容生成唯一 MD5，避免确定性伪视频 MD5 在多次运行间残留记录
    污染预检断言；因此即便库清理失败，测试间也不会相互干扰。
    """
    from app.config import load_config
    from app.db_models import UploadedVideo
    from app.services import db_service

    cfg = load_config()
    up = cfg.data_path / "uploads"
    if up.exists():
        for child in up.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
    try:
        with db_service.session() as s:
            s.query(UploadedVideo).delete()
            s.commit()
    except Exception:
        pass
    yield



def _client() -> TestClient:
    return TestClient(app)


def _make_video(size: int = 12 * 1024 * 1024) -> bytes:
    # 伪视频内容（非真实视频，仅用于链路校验）；附加随机前缀确保各次/各用例 MD5 唯一
    base = b"TENNISCLIP-TEST-VIDEO-CONTENT-"
    prefix = os.urandom(16)
    return prefix + (base * ((size // len(base)) + 1))[:size]


def _upload_full(client: TestClient, data: bytes, md5: str, size: int):
    """走完 check→chunk*→complete 全流程，返回 complete 响应。"""
    chunk = 5 * 1024 * 1024
    n = max(1, (size + chunk - 1) // chunk)
    # 首片带 total/size 建立会话，其余片仅带 md5/index
    for i in range(n):
        part = data[i * chunk : (i + 1) * chunk]
        crc = format(zlib.crc32(part) & 0xFFFFFFFF, "x")
        form = {
            "md5": md5,
            "index": i,
            "size_bytes": size,
            "total": n,
            "original_name": "test.mp4",
            "crc32": crc,
            "length": len(part),
        }
        files = {"file": (f"p{i}", part, "application/octet-stream")}
        r = client.post("/api/v1/upload/chunk", data=form, files=files)
        assert r.status_code == 200, r.text
    return client.post("/api/v1/upload/complete", json={"md5": md5, "size_bytes": size})


def test_check_miss_then_hit():
    client = _client()
    data = _make_video()
    md5 = hashlib.md5(data).hexdigest()
    size = len(data)

    # 未上传前预检应为未命中
    r = client.post("/api/v1/upload/check", json={"md5": md5, "size_bytes": size})
    assert r.status_code == 200
    body = r.json()
    assert body["hit"] is False
    assert body["chunk"]["enabled"] is True
    assert body["chunk"]["size_bytes"] == 5 * 1024 * 1024

    # 完整上传后再次预检应命中（秒传）
    comp = _upload_full(client, data, md5, size)
    assert comp.status_code == 200
    assert comp.json()["md5"] == md5

    r2 = client.post("/api/v1/upload/check", json={"md5": md5, "size_bytes": size})
    assert r2.json()["hit"] is True

    # size 不一致视为未命中（校验防篡改）
    r3 = client.post("/api/v1/upload/check", json={"md5": md5, "size_bytes": size + 1})
    assert r3.json()["hit"] is False


def test_chunk_progress_and_resume():
    client = _client()
    data = _make_video(size=11 * 1024 * 1024)  # 3 片
    md5 = hashlib.md5(data).hexdigest()
    size = len(data)
    chunk = 5 * 1024 * 1024
    n = 3

    # 仅上传第 0、2 片（模拟断点，第 1 片缺失）
    for i in (0, 2):
        part = data[i * chunk : (i + 1) * chunk]
        crc = format(zlib.crc32(part) & 0xFFFFFFFF, "x")
        form = {"md5": md5, "index": i, "size_bytes": size, "total": n, "original_name": "t.mp4", "crc32": crc, "length": len(part)}
        r = client.post("/api/v1/upload/chunk", data=form, files={"file": (f"p{i}", part, "application/octet-stream")})
        assert r.status_code == 200

    prog = client.get("/api/v1/upload/chunks", params={"md5": md5}).json()
    assert set(prog["ok"]) == {0, 2}
    assert prog["missing"] == [1]

    # 补齐第 1 片（断点续传）
    part1 = data[chunk : 2 * chunk]
    crc1 = format(zlib.crc32(part1) & 0xFFFFFFFF, "x")
    r1 = client.post(
        "/api/v1/upload/chunk",
        data={"md5": md5, "index": 1, "crc32": crc1, "length": len(part1)},
        files={"file": ("p1", part1, "application/octet-stream")},
    )
    assert r1.status_code == 200

    prog2 = client.get("/api/v1/upload/chunks", params={"md5": md5}).json()
    assert prog2["missing"] == []

    comp = client.post("/api/v1/upload/complete", json={"md5": md5, "size_bytes": size})
    assert comp.status_code == 200
    assert comp.json()["md5"] == md5


def test_complete_md5_mismatch_returns_409():
    client = _client()
    data = _make_video()
    md5 = hashlib.md5(data).hexdigest()
    size = len(data)
    chunk = 5 * 1024 * 1024
    n = max(1, (size + chunk - 1) // chunk)

    for i in range(n):
        part = data[i * chunk : (i + 1) * chunk]
        # 故意篡改第 0 片内容，使整文件 MD5 与声明不符
        if i == 0:
            part = bytes(reversed(part))
        crc = format(zlib.crc32(part) & 0xFFFFFFFF, "x")
        form = {"md5": md5, "index": i, "size_bytes": size, "total": n, "original_name": "x.mp4", "crc32": crc, "length": len(part)}
        client.post("/api/v1/upload/chunk", data=form, files={"file": (f"p{i}", part, "application/octet-stream")})

    comp = client.post("/api/v1/upload/complete", json={"md5": md5, "size_bytes": size})
    assert comp.status_code == 409


def test_process_by_md5_reuses_stored_file():
    client = _client()
    data = _make_video(size=6 * 1024 * 1024)
    md5 = hashlib.md5(data).hexdigest()
    size = len(data)

    comp = _upload_full(client, data, md5, size)
    assert comp.status_code == 200

    # 以 md5 提交处理任务（秒传：复用已落盘视频重新分析）
    r = client.post("/api/v1/process", data={"md5": md5, "level": "beginner"})
    assert r.status_code == 200
    body = r.json()
    assert "task_id" in body
    assert body["status"] in ("pending", "running", "succeeded", "failed")


def test_process_unknown_md5_400():
    client = _client()
    r = client.post("/api/v1/process", data={"md5": "0" * 32})
    assert r.status_code == 400
