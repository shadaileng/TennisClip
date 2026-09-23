"""CV 感知层公共运行时：依赖探测、权重解析、模型加载（方案 12 · Step 2）。

六个 cv_* 薄封装共享的失败契约：
- 依赖缺失 / 权重缺失 / 权重不可执行 → 抛 ``CvUnavailable``（RuntimeError 子类），
  消息携带安装或放置指引；
- ``detect.*`` 节点 ``on_failure="skip"`` 捕获后级联降级，任务不失败；
- ``post.*`` 节点按方案保持默认 ``fail``（CLIP 初筛/击球分类是其唯一能力，降级无意义）。

权重根目录固定为 ``backend/data/models/``（data/ 已被 .gitignore 整体忽略，
权重文件不入库）。
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

# 权重根目录：app/utils/cv_runtime.py → parents[2] = backend/
MODELS_DIR = Path(__file__).resolve().parents[2] / "data" / "models"


class CvUnavailable(RuntimeError):
    """CV 依赖或权重不可用（节点级降级信号，非代码缺陷）。"""


def require(module: str, extra: str = "cv") -> Any:
    """按需导入重依赖；缺失抛 CvUnavailable（含安装指引）。

    全部 cv_* 模块只在函数体内调用本函数，模块级导入保持零依赖，
    保证节点注册与 schema 构建在无 CV 环境下照常工作。
    """
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise CvUnavailable(
            f"缺少 CV 依赖 {module}（{exc}），请安装：uv sync --extra {extra}"
        ) from exc


def resolve_weights(default_name: str, explicit: str = "", hint: str = "") -> Path:
    """解析权重文件路径：显式路径 > 权重根目录默认名；不存在抛 CvUnavailable。"""
    path = Path(explicit).expanduser() if explicit else MODELS_DIR / default_name
    if not path.is_file():
        raise CvUnavailable(
            f"未找到权重文件 {path}"
            + (f"；{hint}" if hint else f"；请放置于 {MODELS_DIR}/")
        )
    return path


def load_torch_model(path: Path) -> Any:
    """加载可执行模型：优先 TorchScript，回退 torch.save 完整模型。

    约定（TrackNet/球场检测器等自训练权重的统一导出格式）：
        torch.jit.script(model) 或 torch.save(model, path)
    state_dict 裸权重不支持——需先导出为上述格式，报错文案会给出指引。
    """
    torch = require("torch")
    try:
        return torch.jit.load(str(path)).eval()
    except Exception:  # noqa: BLE001  非 TorchScript，转普通加载
        pass
    try:
        obj = torch.load(str(path), map_location="cpu", weights_only=False)
    except Exception as exc:  # noqa: BLE001
        raise CvUnavailable(
            f"权重 {path} 加载失败（{exc}）；请导出为 TorchScript "
            f"或 torch.save(完整模型) 后重试"
        ) from exc
    if hasattr(obj, "eval") and callable(getattr(obj, "forward", None)):
        return obj.eval()
    if isinstance(obj, dict) and hasattr(obj.get("model"), "eval"):
        return obj["model"].eval()
    raise CvUnavailable(
        f"权重 {path} 不是可执行模型（state_dict/裸字典不支持）；"
        f"请用 torch.jit.script(model) 或 torch.save(model, path) 导出"
    )


def sample_frames(
    video_path: Path, start: float, end: float, count: int,
    size: tuple[int, int] = (224, 224),
) -> list:
    """在 [start, end) 均匀采样约 count 帧，返回 np.ndarray 列表 (H, W, 3) uint8。

    cv_clip_score / cv_stroke_cls 共用的 ffmpeg 抽帧路径；
    视频不可读/ffmpeg 缺失 → 抛 CvUnavailable。
    """
    np = require("numpy")
    from app.utils import ffmpeg

    if not ffmpeg.is_available():
        raise CvUnavailable("ffmpeg 不可用，无法抽帧（请安装 ffmpeg）")
    dur = max(float(end) - float(start), 0.1)
    fps = min(max(count / dur, 0.1), 60.0)
    w, h = size
    try:
        raw = ffmpeg.run(
            [
                "ffmpeg", "-y", "-ss", f"{max(0.0, start):.3f}",
                "-i", str(video_path), "-t", f"{dur:.3f}",
                "-vf", f"fps={fps:.4f},scale={w}:{h}",
                "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
            ],
            timeout=120,
            binary=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise CvUnavailable(f"抽帧失败（{video_path}）：{exc}") from exc
    frame_size = w * h * 3
    n = len(raw) // frame_size if raw else 0
    return [
        np.frombuffer(raw[i * frame_size:(i + 1) * frame_size], dtype=np.uint8)
        .reshape(h, w, 3)
        for i in range(n)
    ]
