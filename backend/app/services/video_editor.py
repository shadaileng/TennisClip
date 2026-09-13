"""自动剪辑合成：按时间戳拼接高光片段，渲染为标准 15 秒集锦。

实现说明：
- 使用 FFMPEG concat 协议拼接各片段，再统一缩放到目标时长；
- 输出固定 target_duration 秒的 MP4（H.264 + AAC），可直接用于短视频发布；
- 片段不足目标时长时按置信度降序重复补齐；片段超长时自动裁剪尾部。
"""

from __future__ import annotations

from pathlib import Path

from app.config import AppConfig
from app.models import HighlightResult
from app.utils import ffmpeg


def edit_highlight_video(
    source: Path,
    highlight: HighlightResult,
    config: AppConfig,
) -> Path:
    """渲染 15 秒高光集锦，返回输出文件路径。"""
    out = config.ensure_output_dir() / f"{source.stem}_highlight_{config.highlight.target_duration}s.mp4"

    if not ffmpeg.is_available():
        # 降级：复制源视频前 target_duration 秒作为占位，保证链路可跑通
        raise RuntimeError("FFMPEG 不可用，无法渲染集锦。请安装 ffmpeg 后重试。")

    work_dir = out.parent / f"{source.stem}_concat_list.txt"
    durations = []
    remaining = float(config.highlight.target_duration)

    for seg in sorted(highlight.segments, key=lambda s: -s.confidence):
        take = min(seg.end - seg.start, remaining)
        if take <= 0:
            continue
        remaining -= take
        durations.append(take)
        if remaining <= 0:
            break

    if not durations:
        # 无可用片段时退化为源视频前 15 秒
        durations = [float(config.highlight.target_duration)]

    # 逐段抽剪后用 concat 合并，控制时长更精确
    seg_files = []
    remaining = float(config.highlight.target_duration)
    ordered = sorted(highlight.segments, key=lambda s: -s.confidence)
    ordered += [s for s in ordered]  # 不足时允许重复补齐
    idx = 0
    for i in range(remaining * 3):
        if remaining <= 0 or idx >= len(ordered):
            break
        seg = ordered[idx]
        take = min(seg.end - seg.start, remaining)
        if take < 0.5:
            idx += 1
            continue
        seg_path = out.parent / f"{source.stem}_seg{idx:02d}.mp4"
        ffmpeg.run([
            "ffmpeg", "-y", "-ss", f"{seg.start:.3f}", "-i", str(source), "-t", f"{take:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", str(seg_path),
        ])
        seg_files.append(seg_path)
        remaining -= take
        idx += 1

    if not seg_files:
        seg_files = [source]

    if len(seg_files) == 1:
        ffmpeg.run([
            "ffmpeg", "-y", "-i", str(seg_files[0]),
            "-t", str(config.highlight.target_duration),
            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", str(out),
        ])
    else:
        list_file = out.parent / f"{source.stem}_concat.txt"
        list_file.write_text(
            "".join(f"file '{p.name}'\n" for p in seg_files), encoding="utf-8"
        )
        ffmpeg.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-t", str(config.highlight.target_duration),
            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", str(out),
        ])
        list_file.unlink(missing_ok=True)

    for p in seg_files:
        p.unlink(missing_ok=True)

    return out
