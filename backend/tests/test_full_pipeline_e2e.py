"""端到端管线测试：用视频跑完整链路（预处理 → 高光识别 → 剪辑 → 报告）。

约定：
- 依赖 conftest 的 TENNISCLIP_ENV=test / TENNISCLIP_DATA_DIR=data_test，数据与真实 data/ 隔离。
- 强制 llm.mock_mode='always'，不依赖真实 Step 3.7 Flash API / 网络。
- FFMPEG 不可用时跳过（剪辑节点强依赖系统 ffmpeg）。
- 样本视频来源（按优先级）：
  1) 环境变量 E2E_SAMPLE_VIDEO 指向的 mp4；
  2) 默认路径 data_test/outputs/uploads/9a9a0c89dd6f.mp4（本地存在则用真实视频）；
  3) 以上皆缺失时，用 ffmpeg 生成一段合成视频兜底；
  4) 连合成都失败时 pytest.skip，避免门禁因“找不到视频文件”而挂掉。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import pytest

from app.config import AppConfig, load_config
from app.core import run_pipeline
from app.models import TaskResult, TaskStatus
from app.services import db_service
from app.utils import ffmpeg
from app.utils.logger import get_logger

logger = get_logger(__name__)

SAMPLE_VIDEO = Path(
    os.environ.get(
        "E2E_SAMPLE_VIDEO",
        "/workspace/backend/data_test/outputs/uploads/9a9a0c89dd6f.mp4",
    )
)


def _make_synthetic_video(config: AppConfig) -> Optional[Path]:
    """真实样本缺失时，用 ffmpeg 生成一段带音频的合成测试视频兜底。"""
    if not ffmpeg.is_available():
        return None
    dest = config.ensure_output_dir() / "e2e_synthetic_sample.mp4"
    try:
        ffmpeg.run([
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=3:size=1280x720:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-pix_fmt", "yuv420p",
            "-c:v", "libx264", "-c:a", "aac",
            str(dest),
        ])
    except Exception as exc:  # noqa: BLE001 - 兜底失败仅跳过测试
        logger.warning("合成视频生成失败，端到端测试将跳过: {}", exc)
        return None
    return dest if dest.exists() else None


@pytest.mark.skipif(not ffmpeg.is_available(), reason="FFMPEG 不可用，跳过端到端剪辑测试")
def test_full_pipeline_e2e_on_real_video():
    # 测试环境配置：落在 data_test/，不触碰真实 data/
    config = load_config()

    # 使用独立干净测试库，避免 data_test 中历史脏库（缺 model_providers 等表、
    # 且 alembic_version 已存在导致 upgrade head 被跳过）干扰端到端验证。
    import app.services.db_service as _dbs

    e2e_db = config.data_path / "e2e_test.db"
    e2e_db.unlink(missing_ok=True)
    config.database.url = f"sqlite:///{e2e_db}"
    # 重置 db_service 缓存的引擎，使 init_db 用新 url 重新建库
    _dbs._engine = None
    _dbs._SessionLocal = None

    db_service.init_db(config)

    # 强制 Mock 模式，确保不发起真实 LLM 网络请求
    config.llm.mock_mode = "always"

    # 解析样本视频：默认/环境变量路径 → 缺失则生成合成视频 → 再不行则跳过
    video = Path(SAMPLE_VIDEO)
    if not video.is_absolute():
        video = config.data_path / video
    if not video.exists():
        generated = _make_synthetic_video(config)
        if generated is None:
            pytest.skip(
                "未找到样本视频且无法生成合成视频，跳过端到端测试。"
                "如需基于真实视频运行，请设置 E2E_SAMPLE_VIDEO 指向一个 mp4 文件。"
            )
        video = generated

    task_id = "e2e-real-video"
    result = TaskResult(task_id=task_id, source_video=str(video))

    run_pipeline(video, config, result, level="intermediate")

    # 1) 管线整体成功
    assert result.status == TaskStatus.SUCCEEDED, f"管线失败: {result.error}"

    # 2) 高光识别产出
    assert result.highlight is not None, "未产出高光结果"
    assert len(result.highlight.segments) >= 1, "高光片段为空"

    # 3) 剪辑产出集锦视频文件
    assert result.highlight_video_path, "未生成集锦视频路径"
    assert Path(result.highlight_video_path).exists(), (
        f"集锦视频文件不存在: {result.highlight_video_path}"
    )

    # 4) 技术分析报告产出
    assert result.report is not None, "未产出报告"
    assert result.report.summary, "报告 summary 为空"
    assert result.report_path, "未生成报告路径"
    assert Path(result.report_path).exists(), (
        f"报告文件不存在: {result.report_path}"
    )

    # 5) 数据库终态已落库
    with db_service.session() as s:
        from app.db_models import Task as TaskRow

        row = s.query(TaskRow).filter_by(task_id=task_id).first()
        assert row is not None, "任务未落库"
        assert row.status == "succeeded", f"库内状态异常: {row.status}"
