"""FFMPEG 探测与命令封装。"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)

_available: Optional[bool] = None


def is_available() -> bool:
    global _available
    if _available is None:
        _available = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
        logger.info("ffmpeg available: {}", _available)
    return _available


def run(cmd: list[str], timeout: int = 300, capture_stderr: bool = False, binary: bool = False) -> "str | bytes":
    """执行命令，失败时抛 RuntimeError。

    capture_stderr: 返回 stderr 而非 stdout（如 showinfo 元数据打印到 stderr）。
    binary: 以二进制模式捕获（如 rawvideo 帧流），返回 bytes。
    """
    if not is_available():
        raise RuntimeError("FFMPEG 未安装或不在 PATH 中")
    logger.debug("exec: {}", " ".join(cmd))
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=not binary,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        err = proc.stderr if isinstance(proc.stderr, str) else (proc.stderr or b"").decode("utf-8", "replace")
        tail = err[-2000:]
        raise RuntimeError(f"FFMPEG 失败 (exit={proc.returncode}): {tail}")
    if binary:
        return proc.stdout  # bytes
    return proc.stderr if capture_stderr else proc.stdout


def probe(path: Path) -> dict:
    """用 ffprobe 探测视频元信息（时长/分辨率/帧率）。"""
    out = run([
        "ffprobe", "-v", "error",
        "-show_format", "-show_streams",
        "-of", "json", str(path),
    ])
    data = json.loads(out)
    result: dict = {}
    fmt = data.get("format", {})
    result["duration"] = float(fmt.get("duration", 0) or 0)
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            result["width"] = int(stream.get("width", 0))
            result["height"] = int(stream.get("height", 0))
            fps = stream.get("avg_frame_rate", "30/1")
            num, _, den = fps.partition("/")
            if num and den:
                result["fps"] = round(float(num) / float(den), 3)
            break
    return result
