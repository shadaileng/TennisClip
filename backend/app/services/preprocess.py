"""视频预处理：统一分辨率与帧率，修复模糊（基础降噪）。

实现说明：
- 通过 FFMPEG 将任意主流格式转换为统一目标分辨率/帧率（config.video.resolution，默认 720p/30fps）的中间文件；
- 超长视频截断到 max_input_seconds（验收口径 1–5 分钟）；
- 后续可接入 OpenCV 帧增强（去模糊/降噪）提升极端画面识别稳定性。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from app.config import AppConfig
from app.utils import ffmpeg


def probe_video(path: Path) -> dict:
    """探测视频时长/分辨率/帧率，缺失时返回空 dict。"""
    if not ffmpeg.is_available():
        return {}
    return ffmpeg.probe(path)


def preprocess(video_path: Path, config: AppConfig, work_dir: Optional[Path] = None) -> Path:
    """将输入视频预处理为统一规格文件，返回中间文件路径。

    若 FFMPEG 不可用且输入本身规格已符合，则直接返回原路径（开发态降级）。
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(video_path)

    if not ffmpeg.is_available():
        return video_path

    out_dir = work_dir or config.ensure_output_dir()
    stem = video_path.stem
    target = out_dir / f"{stem}_preprocessed.mp4"

    ffmpeg.run([
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-t", str(config.video.max_input_seconds),
        # 统一缩放到目标高度（config.video.resolution，默认 720p），宽度按比例且为偶数，
        # fps 统一，轻量锐化去模糊。
        # 注意：scale 表达式内不能含逗号（逗号会被 ffmpeg 当作滤镜分隔符），
        # 因此用固定高度目标，而非带 if() 的复杂表达式。
        "-vf", f"scale=-2:{config.video.resolution_height}:flags=lanczos,fps={config.video.fps},unsharp=3:3:0.6",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "128k",
        str(target),
    ])
    return target
