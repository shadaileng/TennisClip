"""上传服务门面：MD5 去重落盘 + 分片会话（data.bin + manifest）。

裁剪自 TennisDiary 的 file_service 分片逻辑（见 docs/reference/TennisDiary），
但去除 user_id（TennisClip 为本地单机工具，无多租户）。

路由层只调用本模块，禁止自行拼 uploads 路径或写盘。

存储布局：
- 成品：{data_path}/uploads/{md5}{ext}（同内容单份，MD5 命名）
- 分片会话：{data_path}/uploads/_chunks/{md5}/{data.bin, manifest.json}
  - `data.bin` 按 offset = index*chunk_size 定位写入，本身就是最终文件，complete 免合并
  - `manifest.json` 为唯一权威账本：写盘 + 校验 + fsync 通过才入账 ok，失败入 failed
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import zlib
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.config import AppConfig
from app.db_models import UploadedVideo
from app.utils.logger import get_logger

log = get_logger(__name__)

# 分片策略（服务端单一真源，与 TennisDiary 默认一致）
CHUNK_SIZE_BYTES = 5 * 1024 * 1024
CHUNK_THRESHOLD_BYTES = 20 * 1024 * 1024
MAX_CHUNK_COUNT = 512
CHUNK_MANIFEST_VERSION = 1
CHUNK_FALLBACK_EXT = ".mp4"
# data.bin 单文件上限（与 config 的 MAX_UPLOAD 概念对齐，这里默认 2GB 兜底）
CHUNK_MAX_BYTES = CHUNK_SIZE_BYTES * 4

_MD5_RE = re.compile(r"^[0-9a-f]{32}$")
_CRC32_RE = re.compile(r"^[0-9a-f]{1,8}$")

# 分片写盘的全局串行锁（本地单机工具，避免并发定位写互相覆盖）
_write_lock = threading.Lock()


class ChunkParamError(ValueError):
    """分片参数非法（→ 400）"""


class ChunkSessionError(ValueError):
    """分片会话不存在（→ 400）：客户端需从 index=0 重开会话"""


class ChunkVerifyError(ValueError):
    """片级校验失败（→ 400）：该片已入账 failed，可只重传该片"""


class ChunkIncompleteError(ValueError):
    """分片未齐全（→ 400）：响应附 missing 列表"""


class ChunkMismatchError(ValueError):
    """整文件 MD5 / size 不符（→ 409）"""


class ChunkTooLargeError(ValueError):
    """超过上限（→ 413）"""


# -------------------- 路径推导 --------------------


def _upload_root(config: AppConfig) -> Path:
    root = config.data_path / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _chunk_dir(config: AppConfig, md5: str) -> Path:
    d = _upload_root(config) / "_chunks" / md5
    d.mkdir(parents=True, exist_ok=True)
    return d


def final_path(config: AppConfig, md5: str, ext: str) -> Path:
    """已落盘成品的绝对路径：{md5}{ext}"""
    if not ext:
        ext = CHUNK_FALLBACK_EXT
    return _upload_root(config) / f"{md5}{ext}"


def _data_bin(config: AppConfig, md5: str) -> Path:
    return _chunk_dir(config, md5) / "data.bin"


def _manifest_path(config: AppConfig, md5: str) -> Path:
    return _chunk_dir(config, md5) / "manifest.json"


# -------------------- manifest 读写 --------------------


def _read_manifest(config: AppConfig, md5: str) -> Optional[dict]:
    p = _manifest_path(config, md5)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _write_manifest(config: AppConfig, md5: str, manifest: dict) -> None:
    p = _manifest_path(config, md5)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)  # 原子替换，避免半写


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# -------------------- 秒传预检 --------------------


def find_by_md5(db: Session, md5: str) -> Optional[UploadedVideo]:
    """按 MD5 查已上传视频记录（物理文件缺失视为未命中）。"""
    rec = db.query(UploadedVideo).filter_by(md5=md5).first()
    return rec


def exists_physically(config: AppConfig, rec: UploadedVideo) -> bool:
    """记录对应的物理文件是否存在。"""
    return (config.data_path / rec.rel_path).exists()


def check_md5(db: Session, config: AppConfig, md5: str, size_bytes: int = 0) -> bool:
    """秒传预检：库有记录且物理文件存在，且 size 一致（size=0 跳过）。"""
    if not _MD5_RE.match(md5 or ""):
        return False
    rec = find_by_md5(db, md5)
    if rec is None:
        return False
    if not exists_physically(config, rec):
        return False
    if size_bytes > 0 and rec.size_bytes and rec.size_bytes != size_bytes:
        return False
    return True


# -------------------- 整文件登记（MD5 去重） --------------------


def register_file(
    db: Session,
    config: AppConfig,
    content: bytes,
    *,
    ext: str = "",
    original_name: str = "",
) -> tuple[UploadedVideo, bool]:
    """登记一个已落盘（或内存）的视频：写盘 + 落库（同 MD5 只保留一条记录）。

    Returns:
        (UploadedVideo 记录, 是否复用了既有记录)
    """
    md5 = hashlib.md5(content).hexdigest()
    if not ext:
        suffix = os.path.splitext(original_name or "")[1].lower()
        ext = suffix or CHUNK_FALLBACK_EXT
    size_bytes = len(content)

    rec = find_by_md5(db, md5)
    if rec is not None:
        if not exists_physically(config, rec):
            final_path(config, md5, rec.ext).write_bytes(content)
        if not rec.size_bytes:
            rec.size_bytes = size_bytes
        if not rec.original_name and original_name:
            rec.original_name = original_name
        db.flush()
        log.info("登记复用已有视频: md5={} rel={}", md5[:12], rec.rel_path)
        return rec, True

    dest = final_path(config, md5, ext)
    dest.write_bytes(content)
    rec = UploadedVideo(
        md5=md5,
        ext=ext,
        size_bytes=size_bytes,
        original_name=original_name or f"video{ext}",
        rel_path=str(dest.relative_to(config.data_path)),
        created_at=time.time(),
    )
    db.add(rec)
    try:
        db.flush()
    except Exception:
        db.rollback()
        existing = find_by_md5(db, md5)
        if existing is not None:
            return existing, True
        raise
    log.info("登记新视频: md5={} rel={} size={}", md5[:12], rec.rel_path, size_bytes)
    return rec, False


# -------------------- 分片策略 --------------------


def chunk_policy() -> dict:
    """分片策略（服务端单一真源）"""
    return {
        "enabled": True,
        "size_bytes": CHUNK_SIZE_BYTES,
        "threshold_bytes": CHUNK_THRESHOLD_BYTES,
        "max_count": MAX_CHUNK_COUNT,
        "crc32": True,
    }


def chunk_count_of(total_size: int, chunk_size: int) -> int:
    if chunk_size <= 0:
        raise ChunkParamError(f"非法单片大小: {chunk_size}")
    return max(1, -(-int(total_size) // int(chunk_size)))


def validate_chunk_params(md5: str, index: int, total: int) -> None:
    if not _MD5_RE.match(md5 or ""):
        raise ChunkParamError(f"非法 md5: {md5!r}")
    if int(total) < 1 or int(total) > MAX_CHUNK_COUNT:
        raise ChunkParamError(f"分片总数越界: {total}")
    if int(index) < 0 or int(index) >= int(total):
        raise ChunkParamError(f"分片索引越界: index={index} total={total}")


# -------------------- 分片会话 --------------------


def open_chunk_session(
    config: AppConfig,
    md5: str,
    *,
    total_size: int,
    chunk_size: int = 0,
    total: int = 0,
    original_name: str = "",
    ext: str = "",
) -> dict:
    """创建/复用分片会话（幂等）。已有会话时校验关键参数一致后复用。"""
    if not _MD5_RE.match(md5 or ""):
        raise ChunkParamError(f"非法 md5: {md5!r}")
    size = int(total_size)
    if size <= 0:
        raise ChunkParamError(f"非法文件总大小: {total_size}")
    unit = int(chunk_size) or CHUNK_SIZE_BYTES
    if unit <= 0:
        raise ChunkParamError(f"非法单片大小: {chunk_size}")
    count = int(total) or chunk_count_of(size, unit)
    if count > MAX_CHUNK_COUNT:
        raise ChunkParamError(f"分片数超过上限: {count} > {MAX_CHUNK_COUNT}")

    existing = _read_manifest(config, md5)
    if existing:
        conflict = (
            int(existing.get("total_size") or 0) != size
            or int(existing.get("chunk_size") or 0) != unit
            or int(existing.get("total") or 0) != count
        )
        if conflict:
            raise ChunkParamError("分片会话参数与已存在会话不一致")
        return existing

    now = _utc_now_iso()
    manifest = {
        "v": CHUNK_MANIFEST_VERSION,
        "md5": md5,
        "total_size": size,
        "chunk_size": unit,
        "total": count,
        "ext": (ext or os.path.splitext(original_name or "")[1].lower() or CHUNK_FALLBACK_EXT),
        "original_name": original_name,
        "segments": {},
        "created_at": now,
        "updated_at": now,
    }
    _write_manifest(config, md5, manifest)
    log.info("创建分片会话: md5={} total={} size={}", md5[:12], count, size)
    return manifest


def register_chunk(
    config: AppConfig,
    md5: str,
    index: int,
    content: bytes,
    *,
    length: int = 0,
    crc32: str = "",
) -> dict:
    """接收并落盘一个分片：片级校验 → 定位写 → fsync → 入账。

    顺序不可调换：只有写盘成功后才入账 ok；任何校验/写盘失败都入账 failed
    并抛 ChunkVerifyError，客户端可只重传该片。
    """
    manifest = _require_session(config, md5)
    validate_chunk_params(md5, index, int(manifest.get("total") or 0))

    if len(content) > CHUNK_MAX_BYTES:
        raise ChunkParamError(f"单片超过上限: {len(content)} > {CHUNK_MAX_BYTES}")

    unit = int(manifest.get("chunk_size") or 0)
    total_size = int(manifest.get("total_size") or 0)
    offset = int(index) * unit
    expect = min(unit, total_size - offset)

    if len(content) != expect or (length and int(length) != expect):
        _reject_segment(config, md5, manifest, index, "length_mismatch")

    if crc32:
        if not _CRC32_RE.match(crc32.lower()):
            _reject_segment(config, md5, manifest, index, "crc_invalid")
        if int(crc32, 16) != (zlib.crc32(content) & 0xFFFFFFFF):
            _reject_segment(config, md5, manifest, index, "crc_mismatch")

    try:
        _pwrite_chunk(config, md5, offset, content)
    except (OSError, ValueError) as exc:
        log.error("分片写入失败: md5={} index={} err={}", md5[:12], index, exc)
        _reject_segment(config, md5, manifest, index, "write_error")

    _mark_segment(config, md5, manifest, index, "ok", size=len(content), crc32=crc32.lower())
    return _summary(manifest)


def list_chunks(config: AppConfig, md5: str) -> dict:
    """分片会话进度：ok / failed / missing 三集合（断点续传依据）。"""
    if not _MD5_RE.match(md5 or ""):
        raise ChunkParamError(f"非法 md5: {md5!r}")
    manifest = _read_manifest(config, md5)
    if manifest is None:
        return _empty_summary()
    return _summary(manifest)


def complete_chunk_upload(
    db: Session,
    config: AppConfig,
    md5: str,
    size_bytes: int = 0,
) -> tuple[UploadedVideo, bool]:
    """合并校验并登记：段齐全 → 整文件 MD5 → 写盘成品 + 落库（免合并）。

    Returns:
        (UploadedVideo 记录, 是否复用了既有记录)
    """
    if not _MD5_RE.match(md5 or ""):
        raise ChunkParamError(f"非法 md5: {md5!r}")
    manifest = _require_session(config, md5)
    summary = _summary(manifest)
    total_size = int(manifest.get("total_size") or 0)

    data_abs = _data_bin(config, md5)
    if summary["missing"] or not data_abs.exists() or data_abs.stat().st_size != total_size:
        raise ChunkIncompleteError(
            "分片未齐全: 缺 {} 片 {}".format(len(summary["missing"]), summary["missing"][:10])
        )

    real_md5, real_size = _md5_and_size_of(data_abs)
    declared_md5 = str(manifest.get("md5") or "")
    declared_size = int(size_bytes) if size_bytes else total_size
    if not real_md5 or real_md5 != declared_md5 or real_size != declared_size:
        _clear_unverified_segments(config, md5, manifest)
        raise ChunkMismatchError(
            "整文件校验不符: 期望 {} ({}B)，实际 {} ({}B)".format(
                declared_md5[:12], declared_size, (real_md5 or "")[:12], real_size
            )
        )

    ext = str(manifest.get("ext") or CHUNK_FALLBACK_EXT)
    original_name = str(manifest.get("original_name") or "")

    dest = final_path(config, md5, ext)
    if not dest.exists() or dest.stat().st_size != real_size:
        # 把 data.bin 迁为成品（同盘 rename，O(1)）
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(data_abs, dest)
        except OSError:
            # 跨文件系统回退拷贝
            dest.write_bytes(data_abs.read_bytes())
            data_abs.unlink(missing_ok=True)
    _clear_chunks(config, md5)

    rec = find_by_md5(db, md5)
    if rec is not None:
        if not exists_physically(config, rec):
            dest.write_bytes(dest.read_bytes())
        if not rec.size_bytes:
            rec.size_bytes = real_size
        if not rec.original_name and original_name:
            rec.original_name = original_name
        db.flush()
        log.info("分片上传完成(复用记录): md5={} rel={}", md5[:12], rec.rel_path)
        return rec, True

    rec = UploadedVideo(
        md5=md5,
        ext=ext,
        size_bytes=real_size,
        original_name=original_name or f"video{ext}",
        rel_path=str(dest.relative_to(config.data_path)),
        created_at=time.time(),
    )
    db.add(rec)
    db.flush()
    log.info("分片上传完成(新记录): md5={} rel={} size={}", md5[:12], rec.rel_path, real_size)
    return rec, False


def chunk_status(config: AppConfig, md5: str) -> dict:
    """/check 下发的分片策略 + 会话进度（服务端单一真源）。"""
    policy = chunk_policy()
    try:
        summary = list_chunks(config, md5)
    except ChunkParamError:
        summary = _empty_summary()
    return {
        **policy,
        "uploaded": summary["ok"],
        "failed": summary["failed"],
        "missing": summary["missing"],
        "total": summary["total"],
        "total_size": summary["total_size"],
        "chunk_size": summary["chunk_size"],
    }


# -------------------- 分片内部实现 --------------------


def _require_session(config: AppConfig, md5: str) -> dict:
    manifest = _read_manifest(config, md5)
    if manifest is None:
        raise ChunkSessionError(f"分片会话不存在: md5={md5}")
    return manifest


def _empty_summary() -> dict:
    return {
        "ok": [],
        "failed": [],
        "missing": [],
        "chunk_size": 0,
        "total": 0,
        "total_size": 0,
        "crc32": True,
    }


def _summary(manifest: dict) -> dict:
    """manifest → 进度三集合：missing = 全部 - ok（failed 段同样需重传）。"""
    segments = manifest.get("segments") or {}
    ok: list[int] = []
    failed: list[int] = []
    for key, entry in segments.items():
        if not str(key).isdigit():
            continue
        state = (entry or {}).get("state")
        if state == "ok":
            ok.append(int(key))
        elif state == "failed":
            failed.append(int(key))
    total = int(manifest.get("total") or 0)
    ok_set = set(ok)
    return {
        "ok": sorted(ok),
        "failed": sorted(failed),
        "missing": [i for i in range(total) if i not in ok_set],
        "chunk_size": int(manifest.get("chunk_size") or 0),
        "total": total,
        "total_size": int(manifest.get("total_size") or 0),
        "crc32": True,
    }


def _mark_segment(config: AppConfig, md5: str, manifest: dict, index: int, state: str, **extra) -> dict:
    """写入一段的入账状态（manifest 是唯一权威真源；写失败仅记日志）。"""
    segments = dict(manifest.get("segments") or {})
    entry = {"state": state, "at": _utc_now_iso()}
    entry.update(extra)
    segments[str(int(index))] = entry
    manifest["segments"] = segments
    manifest["updated_at"] = _utc_now_iso()
    try:
        _write_manifest(config, md5, manifest)
    except (OSError, ValueError) as exc:
        log.error("分片 manifest 入账失败: md5={} index={} err={}", md5[:12], index, exc)
    return manifest


def _reject_segment(config: AppConfig, md5: str, manifest: dict, index: int, reason: str) -> None:
    """入账失败段并抛出（客户端据此只重传该段）。"""
    _mark_segment(config, md5, manifest, index, "failed", reason=reason)
    raise ChunkVerifyError(f"分片校验失败({reason}): index={index}")


def _clear_unverified_segments(config: AppConfig, md5: str, manifest: dict) -> None:
    """整文件校验不符时清账：仅保留通过 crc32 校验的段（无法定位坏片时全部重传）。"""
    segments = manifest.get("segments") or {}
    kept = {
        key: entry
        for key, entry in segments.items()
        if (entry or {}).get("state") == "ok" and (entry or {}).get("crc32")
    }
    manifest["segments"] = kept
    manifest["updated_at"] = _utc_now_iso()
    try:
        _write_manifest(config, md5, manifest)
    except (OSError, ValueError) as exc:
        log.error("分片 manifest 清账失败: md5={} err={}", md5[:12], exc)


def _pwrite_chunk(config: AppConfig, md5: str, offset: int, content: bytes) -> int:
    """定位写分片到 data.bin（含 fsync），返回写入字节数。

    用 os.open(O_RDWR|O_CREAT) 确保首片时文件尚不存在也能创建；
    后续分片按 offset 定位写（稀疏文件，缺口不影响）。
    """
    path = _data_bin(config, md5)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        os.lseek(fd, offset, os.SEEK_SET)
        written = 0
        while written < len(content):
            written += os.write(fd, content[written:])
        os.fsync(fd)
    finally:
        os.close(fd)
    return written


def _data_size(config: AppConfig, md5: str) -> int:
    path = _data_bin(config, md5)
    return path.stat().st_size if path.exists() else 0


def _md5_and_size_of(path: Path) -> tuple[Optional[str], int]:
    """一次读盘计算 MD5 与大小。"""
    h = hashlib.md5()
    size = 0
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
            size += len(block)
    return h.hexdigest(), size


def _clear_chunks(config: AppConfig, md5: str) -> None:
    """清理分片会话目录。"""
    d = _chunk_dir(config, md5)
    try:
        if d.exists():
            for child in d.iterdir():
                child.unlink(missing_ok=True)
            d.rmdir()
    except OSError as exc:
        log.warning("清理分片目录失败: md5={} err={}", md5[:12], exc)
