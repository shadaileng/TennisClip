"""自动剪辑合成：按时间戳拼接高光片段，渲染为标准 15 秒集锦。

实现说明：
- 使用 FFMPEG concat 协议拼接各片段，再统一缩放到目标时长；
- 输出固定 target_duration 秒的 MP4（H.264 + AAC），可直接用于短视频发布；
- 片段不足目标时长时按实际片段合成（不重复补齐）；片段超长时自动裁剪尾部。
"""

from __future__ import annotations

from pathlib import Path

from app.config import AppConfig
from app.models import HighlightResult
from app.utils import ffmpeg
from app.utils.logger import get_logger

logger = get_logger(__name__)


def edit_highlight_video(
    source: Path,
    highlight: HighlightResult,
    config: AppConfig,
) -> Path:
    """渲染高光集锦，返回输出文件路径。

    - 普通档位：按 target_duration 截取前若干高光片段，不足目标时长时按实际片段合成。
    - all 档位（highlight.all_highlights）：遍历整段、拼接全部高光回合，忽略目标时长上限。
    """
    # 输出与源（预处理产物）同目录：源已在 outputs/{task_id}/ 下时即落入任务专属目录，
    # 实现产物按 task_id 隔离；源不在输出目录下（极端降级路径）则回退到输出根目录。
    all_mode = bool(highlight.all_highlights)
    suffix = "all" if all_mode else f"{config.highlight.target_duration}s"
    out_dir = source.parent if str(source.parent).startswith(str(config.ensure_output_dir())) else config.ensure_output_dir()
    out = out_dir / f"{source.stem}_highlight_{suffix}.mp4"

    if not ffmpeg.is_available():
        # 降级：复制源视频前 target_duration 秒作为占位，保证链路可跑通
        raise RuntimeError("FFMPEG 不可用，无法渲染集锦。请安装 ffmpeg 后重试。")

    # 逐段抽剪后用 concat 合并，控制时长更精确。
    # 仅使用 LLM 返回的高光片段，不足目标时长时按实际片段合成（不重复补齐，避免造假循环）。
    seg_files = []
    if all_mode:
        # 所有高光回合：按时间顺序全量截取，每段取完整时长，不做数量/时长取舍。
        ordered = sorted(highlight.segments, key=lambda s: s.start)
        for idx, seg in enumerate(ordered):
            take = seg.end - seg.start
            if take < 0.5:
                continue
            seg_path = out.parent / f"{source.stem}_seg{idx:02d}.mp4"
            ffmpeg.run([
                "ffmpeg", "-y", "-ss", f"{seg.start:.3f}", "-i", str(source), "-t", f"{take:.3f}",
                "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", str(seg_path),
            ])
            seg_files.append(seg_path)
        logger.info("edit: all 模式全段拼接 {} 个回合，忽略目标时长", len(seg_files))
    else:
        remaining = float(config.highlight.target_duration)
        ordered = sorted(highlight.segments, key=lambda s: -s.confidence)
        idx = 0
        for _ in range(int(remaining * 3)):
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

        selected = float(config.highlight.target_duration) - remaining
        if selected < config.highlight.target_duration - 0.5:
            logger.info(
                "edit: 高光片段总时长 {:.1f}s 不足目标 {:.1f}s，按实际片段合成（不重复补齐）",
                selected, config.highlight.target_duration,
            )

    if not seg_files:
        seg_files = [source]

    if len(seg_files) == 1:
        cmd = [
            "ffmpeg", "-y", "-i", str(seg_files[0]),
            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", str(out),
        ]
        if not all_mode:
            # -t 作为输出选项置于 -i 之后、编码参数之前
            cmd = cmd[:3] + ["-t", str(config.highlight.target_duration)] + cmd[3:]
        ffmpeg.run(cmd)
    else:
        list_file = out.parent / f"{source.stem}_concat.txt"
        list_file.write_text(
            "".join(f"file '{p.name}'\n" for p in seg_files), encoding="utf-8"
        )
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", str(out),
        ]
        if not all_mode:
            # -t 作为输出选项置于 -i <list_file> 之后、编码参数之前
            cmd = cmd[:7] + ["-t", str(config.highlight.target_duration)] + cmd[7:]
        ffmpeg.run(cmd)
        list_file.unlink(missing_ok=True)

    for p in seg_files:
        p.unlink(missing_ok=True)

    return out
