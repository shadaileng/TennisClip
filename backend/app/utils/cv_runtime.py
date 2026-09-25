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

from app.utils.logger import get_logger

logger = get_logger(__name__)

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


def resolve_device(prefer: str = "auto") -> str:
    """解析 torch 推理设备：auto=有 CUDA 用 cuda 否则 cpu；显式 cuda/cpu 直用。

    无效值回退 auto 语义。供各 cv_* 薄封装统一选择推理设备（无 GPU 环境自动回退 CPU，
    保证 detect.* 节点 on_failure=skip 降级链在无 GPU 环境照常工作）。

    返回值永远是 ``"cuda"`` 或 ``"cpu"`` 字符串，调用方可直接传给 tensor/model 的 .to()。
    """
    torch = require("torch")
    want = (prefer or "auto").lower()
    if want in ("cuda", "gpu"):
        if torch.cuda.is_available():
            return "cuda"
        logger.warning("cv_runtime: 请求 cuda 但 CUDA 不可用，回退 cpu")
        return "cpu"
    if want in ("cpu",):
        return "cpu"
    # auto / 其他：有 CUDA 用 CUDA，否则 CPU
    return "cuda" if torch.cuda.is_available() else "cpu"


def ensure_device(dev: object) -> str:
    """强约束设备标识为合法字符串（防御：调用方误传 float/None 等非 str 值）。

    返回 ``"cuda"`` 或 ``"cpu"``，非法值记录 warning 并回退 auto 语义。
    此函数应在每个 cv_* 模块使用 dev 之前调用一次，避免 '.to(dev)' 报
    ``'float' object has no attribute 'to'`` 等难以排查的错误。
    """
    if isinstance(dev, str):
        return resolve_device(dev)
    logger.warning(
        "cv_runtime.ensure_device: 收到非 str 设备值 {}（类型 {}），回退 auto",
        repr(dev), type(dev).__name__,
    )
    return resolve_device("auto")


def load_torch_model(path: Path, device: str = "auto") -> Any:
    """加载可执行模型：优先 TorchScript，回退 torch.save 完整模型。

    约定（TrackNet/球场检测器等自训练权重的统一导出格式）：
        torch.jit.script(model) 或 torch.save(model, path)
    state_dict 裸权重不支持——需先导出为上述格式，报错文案会给出指引。

    device：推理设备（auto=有 CUDA 用 CUDA 否则 CPU；显式 cuda/cpu）。
    模型加载后 `.to(device).eval()`，推理调用方张量构造时需 `.to(同设备)`。
    """
    torch = require("torch")
    dev = resolve_device(device)
    import warnings

    with warnings.catch_warnings():
        # torch.jit.load 弃用提示（FutureWarning 引导转 torch.export）与加载失败噪音同级降噪
        warnings.filterwarnings("ignore", category=FutureWarning, module=".*torch.jit.*")
        try:
            model = torch.jit.load(
                str(path), map_location="cpu" if dev == "cpu" else None
            )
            return model.to(dev).eval()
        except Exception:  # noqa: BLE001  非 TorchScript，转普通加载
            pass
    try:
        obj = torch.load(str(path), map_location=dev, weights_only=False)
    except Exception as exc:  # noqa: BLE001
        raise CvUnavailable(
            f"权重 {path} 加载失败（{exc}）；请导出为 TorchScript "
            f"或 torch.save(完整模型) 后重试"
        ) from exc
    if hasattr(obj, "eval") and callable(getattr(obj, "forward", None)):
        return obj.to(dev).eval()
    if isinstance(obj, dict) and hasattr(obj.get("model"), "eval"):
        return obj["model"].to(dev).eval()
    raise CvUnavailable(
        f"权重 {path} 不是可执行模型（state_dict/裸字典不支持）；"
        f"请用 torch.jit.script(model) 或 torch.save(model) 导出"
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
