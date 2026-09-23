"""媒体输入策略（可插拔）：高光识别阶段的视频→模型输入构造。

与 app.services.event_detect 的信号策略模式一致：同一抽象（MediaInputStrategy）+ 注册表分发。

两种策略：
- frame：抽帧（现有行为），将视频抽成 base64 图像帧以 image_url 多图送入；
- video：视频理解（新增），将整段 MP4 以 data URI 内联为单个 video_url 块直送
  （置于文本之前，符合 StepFun 最佳实践），>128MB 仅告警、不切片。

所有策略只负责构造「媒体内容块」列表；prompt 文本由调用方（llm.complete_structured）
负责，并按策略决定文本与媒体块的先后（video 媒体在前、frame 文本在前）。
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from app.config import AppConfig
from app.utils.logger import get_logger

logger = get_logger(__name__)

# StepFun 单文件限制（MB）；超限仅告警、不切片（用户决策：校验告警）
_VIDEO_MAX_MB = 128.0
_VIDEO_MAX_BYTES = int(_VIDEO_MAX_MB * 1024 * 1024)


@runtime_checkable
class MediaInputStrategy(Protocol):
    """媒体输入策略抽象：构造送给 LLM 的媒体内容块列表。"""

    name: str

    def build_media_parts(
        self, video_path: Path, candidates: list, config: AppConfig
    ) -> list[dict]:
        """返回媒体内容块（仅 content 数组元素，不含基础 prompt 文本）。"""
        ...


def _video_to_image_frames(
    video_path: Path, candidates=None, max_frames: int = 30
) -> list[tuple[float, str]]:
    """将本地视频路径转为 (时间戳, base64 图像帧) 列表（OpenAI 多模态 image_url 格式）。

    给定 candidates（信号候选窗口）时：在每段窗口内均匀抽帧；否则全片均匀抽帧。
    无 FFMPEG 时返回空列表（纯文本模式）。
    """
    from app.utils import ffmpeg

    if not ffmpeg.is_available():
        return []
    try:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            probe = ffmpeg.probe(video_path)
            duration = probe.get("duration", 30)
            if candidates:
                samples = []
                per = max(1, max_frames // max(1, len(candidates)))
                for c in candidates:
                    s = max(0.0, float(getattr(c, "start", 0)))
                    e = min(float(duration), float(getattr(c, "end", duration)))
                    if e - s < 0.3:
                        continue
                    for k in range(per):
                        ts = s + (e - s) * (k + 0.5) / per
                        samples.append(ts)
            else:
                step = max(duration / max_frames, 1.0)
                samples = [i * step for i in range(max_frames) if i * step < duration]
            frames = []
            for ts in samples[:max_frames]:
                out = Path(tmp) / f"frame_{len(frames):03d}.jpg"
                ffmpeg.run([
                    "ffmpeg", "-y", "-ss", f"{ts:.2f}", "-i", str(video_path),
                    "-frames:v", "1", "-q:v", "3", str(out),
                ])
                b64 = base64.b64encode(out.read_bytes()).decode()
                frames.append((round(ts, 2), f"data:image/jpeg;base64,{b64}"))
            return frames
    except Exception as exc:  # noqa: BLE001
        logger.warning("视频抽帧失败，降级为纯文本: {}", exc)
        return []


class FrameStrategy:
    """抽帧策略：复用既有抽帧逻辑，输出 image_url 多图块（行为不变）。"""

    name = "frame"

    def build_media_parts(
        self, video_path: Path, candidates: list, config: AppConfig
    ) -> list[dict]:
        frames = _video_to_image_frames(
            video_path, candidates=candidates, max_frames=config.llm.max_frames
        )
        parts: list[dict] = []
        if frames:
            if candidates:
                frame_intro = (
                    f"以下为各候选窗口内抽帧，共 {len(frames)} 帧，已标注对应时间戳（秒）。"
                    f"请据此在候选窗口 ±2s 内给出准确起止秒数。"
                )
            else:
                frame_intro = (
                    f"以下为视频按时长均匀抽帧，共 {len(frames)} 帧，按时间顺序排列，"
                    f"每帧前已标注其对应时间戳（秒）。请依据各帧时间戳给出准确的起止秒数，"
                    f"不要凭空臆造视频中未出现的时间点。"
                )
            parts.append({"type": "text", "text": frame_intro})
            for idx, (ts, frame) in enumerate(frames):
                parts.append({"type": "text", "text": f"[第 {idx + 1} 帧 @ {ts:.1f}s]"})
                parts.append({"type": "image_url", "image_url": {"url": frame}})
        else:
            parts.append({
                "type": "text",
                "text": "（未能抽帧，仅基于文字描述分析）",
            })
        return parts


def _video_to_video_uri(video_path: Path) -> str:
    """读取视频字节并以 base64 data URI 内联（MIME=video/mp4）。"""
    data = video_path.read_bytes()
    b64 = base64.b64encode(data).decode()
    return f"data:video/mp4;base64,{b64}"


def _transcode_for_upload(video_path: Path, max_height: int = 480, crf: int = 28) -> Path:
    """为视频理解上传转一份更小、兼容性更好的副本（降分辨率/码率 + faststart + yuv420p）。

    直送 StepFun 的副本无需高画质：缩小体积可降低 base64 负载、提升远端解码成功率，
    且不影响最终成片（最终剪辑仍用 720p 预处理文件）。转码失败则回退原始文件。

    并发安全（批次 C1，方案 12 · 0.8）：同层并行的多个分析分支可能同时转码同 stem
    视频——先写进程/线程级唯一的 sidecar 临时文件，成功后 os.replace 原子替换到
    共享缓存路径。读者只会读到某个完整文件（POSIX rename 原子），Windows 下目标被
    占用时 os.replace 抛错走回退分支返回原始文件，不会读到写了一半的内容。
    sidecar 以 `.part` 结尾（无容器扩展名），ffmpeg 无法从扩展名推断输出容器，
    须显式 `-f mp4`（否则 exit=234 必走回退、480p 降采样白做）。
    """
    from app.utils import ffmpeg
    if not ffmpeg.is_available():
        return video_path
    import os
    import tempfile
    import threading
    tmp = Path(tempfile.gettempdir()) / f"upload_{video_path.stem}.mp4"
    staging = Path(tempfile.gettempdir()) / (
        f"upload_{video_path.stem}.{os.getpid()}.{threading.get_ident()}.part"
    )
    try:
        ffmpeg.run([
            "ffmpeg", "-y", "-i", str(video_path),
            "-vf", f"scale=-2:{max_height}:flags=lanczos,fps=24",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-c:a", "aac", "-b:a", "48k",
            "-f", "mp4",  # sidecar 无容器扩展名（.part），显式指定容器
            str(staging),
        ])
        if staging.exists() and staging.stat().st_size > 0:
            os.replace(staging, tmp)  # 原子落位共享缓存
            logger.info(
                "视频理解上传副本：{} → {}（{:.1f}MB）",
                video_path.name, tmp.name, tmp.stat().st_size / (1024 * 1024),
            )
            return tmp
    except Exception as exc:  # noqa: BLE001
        logger.warning("视频上传转码失败，回退原始文件: {}", exc)
    finally:
        if staging.exists():
            staging.unlink(missing_ok=True)
    return video_path


class VideoStrategy:
    """视频理解策略：整段 MP4 以 video_url data URI 直送（置于文本之前）。

    >128MB 仅 logger.warning，不切片（用户决策：校验告警）。
    candidates 存在时追加「参考候选窗口（仅供参考）」软提示文本，模型自由定位。
    """

    name = "video"

    def build_media_parts(
        self, video_path: Path, candidates: list, config: AppConfig
    ) -> list[dict]:
        # 视频理解上传副本：降分辨率/码率以缩小 base64 负载、提升 StepFun 远端解码成功率
        # （最终剪辑仍用 720p 预处理文件，不影响成片画质）
        upload_src = _transcode_for_upload(video_path)
        size = upload_src.stat().st_size
        if size > _VIDEO_MAX_BYTES:
            logger.warning(
                "上传视频体积 {:.1f}MB 超过 {}MB 限制，StepFun 可能拒绝：{}",
                size / (1024 * 1024), _VIDEO_MAX_MB, upload_src.name,
            )
        uri = _video_to_video_uri(upload_src)
        parts: list[dict] = [{"type": "video_url", "video_url": {"url": uri}}]
        if candidates:
            cand_lines = "\n".join(
                f"- 候选窗口 {i + 1}：[{c.start:.1f}s, {c.end:.1f}s]"
                for i, c in enumerate(candidates)
            )
            parts.append({
                "type": "text",
                "text": (
                    "以下为信号定位的参考候选窗口（仅供参考，请直接观看整段视频独立判断，"
                    f"不必拘泥于窗口）：\n{cand_lines}"
                ),
            })
        return parts


_STRATEGIES: dict[str, MediaInputStrategy] = {
    "frame": FrameStrategy(),
    "video": VideoStrategy(),
}


def get_strategy(mode: Optional[str]) -> MediaInputStrategy:
    """按模式返回策略；未知/空值回退 frame。"""
    if not mode:
        mode = "frame"
    return _STRATEGIES.get(mode, _STRATEGIES["frame"])
