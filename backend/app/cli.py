"""TennisClip AI 命令行入口。

用法：
  python -m app.cli video.mp4 [--level beginner|intermediate|professional]
  python -m app.cli --batch sample_videos
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from app.config import load_config
from app.core import run_pipeline
from app.models import TaskResult
from app.utils import ffmpeg
from app.utils.logger import get_logger
from app.utils.tasks import TaskQueue


def main() -> int:
    parser = argparse.ArgumentParser(prog="tennisclip", description="网球视频高光集锦 + 技术分析报告工具")
    parser.add_argument("video", nargs="?", help="输入视频文件路径")
    parser.add_argument("--batch", help="批量处理目录（与 video 二选一）")
    parser.add_argument(
        "--level",
        choices=["beginner", "intermediate", "professional"],
        default="intermediate",
        help="分析层级（默认 intermediate）",
    )
    args = parser.parse_args()

    if not args.video and not args.batch:
        parser.error("需要指定视频文件或 --batch 目录")

    config = load_config()
    logger = get_logger("cli")

    if not ffmpeg.is_available():
        logger.warning("FFMPEG 不可用：预处理与剪辑将降级，集锦渲染不可用。请安装 ffmpeg。")

    results: list[TaskResult] = []

    if args.video:
        video = Path(args.video)
        if not video.exists():
            logger.error("视频不存在: %s", video)
            return 1
        result = TaskResult(task_id="cli-0001", source_video=str(video))
        run_pipeline(video, config, result, level=args.level)
        results.append(result)
    else:
        batch_dir = config.path(args.batch)
        if not batch_dir.is_dir():
            logger.error("批量目录不存在: %s", batch_dir)
            return 1
        queue = TaskQueue(config)
        videos = [p for p in sorted(batch_dir.iterdir()) if p.suffix.lower() in (".mp4", ".mov", ".mkv", ".avi")]
        if not videos:
            logger.error("目录中没有视频文件: %s", batch_dir)
            return 1

        for video in videos:
            queue.submit(
                lambda v=video: run_pipeline(
                    v, config, TaskResult(task_id="batch", source_video=str(v)), level=args.level
                )
            )
        # 等待全部完成
        while not all(
            r.status in ("succeeded", "failed", "timeout") for r in queue.get_task_statuses()
        ):
            time.sleep(0.5)
        results = [queue.get(tid) for tid in queue.task_ids()]
        queue.shutdown(wait=True)

    # 汇总输出
    for r in results:
        icon = "OK" if r.status == "succeeded" else "ERR"
        print(f"[{icon}] {r.source_video}  status={r.status}  {r.elapsed_seconds:.1f}s")
        if r.report_path:
            print(f"     报告: {r.report_path}")
        if r.highlight_video_path:
            print(f"     集锦: {r.highlight_video_path}")
        if r.error:
            print(f"     错误: {r.error}")

    ok = sum(1 for r in results if r.status == "succeeded")
    print(f"\n完成 {ok}/{len(results)} 条")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
