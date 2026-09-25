"""CLIP 高光评分薄封装（方案 12 · Step 2.6）。

零样本高光初筛：对每个候选窗口均匀抽帧，计算帧嵌入与高光文本描述的
余弦相似度，``score = clamp(max(帧 × 文本相似度), 0, 1)``。

模型（OpenAI CLIP 系列，transformers 加载，首次使用自动下载）：
- small → openai/clip-vit-base-patch32（最快）
- base  → openai/clip-vit-base-patch16
- large → openai/clip-vit-large-patch14（最准）
"""

from __future__ import annotations

from pathlib import Path

from app.models import Segment
from app.utils.cv_runtime import CvUnavailable, require, sample_frames, ensure_device
from app.utils.logger import get_logger

logger = get_logger(__name__)

MODEL_IDS = {
    "small": "openai/clip-vit-base-patch32",
    "base": "openai/clip-vit-base-patch16",
    "large": "openai/clip-vit-large-patch14",
}
DEFAULT_PHRASES = "exciting tennis rally, winning shot, ace serve, smash"

# 进程内模型缓存（权重只加载一次）
_CACHE: dict[str, tuple] = {}


def _load_clip(model_key: str, device: str):
    transformers = require("transformers")
    torch = require("torch")
    if model_key not in MODEL_IDS:
        raise CvUnavailable(f"未知 CLIP 模型规格：{model_key}，应为 {sorted(MODEL_IDS)}")
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    if model_key not in _CACHE:
        mid = MODEL_IDS[model_key]
        try:
            model = transformers.CLIPModel.from_pretrained(mid)
            processor = transformers.CLIPProcessor.from_pretrained(mid)
        except Exception as exc:  # noqa: BLE001
            raise CvUnavailable(
                f"CLIP 模型 {mid} 加载失败（{exc}）；请联网首次下载或预置 HuggingFace 缓存"
            ) from exc
        _CACHE[model_key] = (model.to(device).eval(), processor, device)
    return _CACHE[model_key]


def score_windows(
    video_path: Path,
    windows: list[Segment],
    model: str = "small",
    phrases: str = DEFAULT_PHRASES,
    frames_per_window: int = 4,
    device: str = "auto",
) -> list[tuple[Segment, float]]:
    """为候选窗口打分，返回 ``[(segment, score)]`` 按分数降序。

    - 依赖 transformers/torch/PIL、模型下载失败 → 抛 CvUnavailable（post 节点按方案 fail）；
    - 窗口过短抽不到帧时该窗口记 0 分（不丢弃，交由 min_score 阈值过滤）。
    """
    require("PIL")
    torch = require("torch")
    dev = ensure_device(device)
    net, processor, device = _load_clip(model, dev)

    texts = [p.strip() for p in phrases.split(",") if p.strip()] or [DEFAULT_PHRASES]
    results: list[tuple[Segment, float]] = []
    for seg in windows:
        frames = sample_frames(video_path, seg.start, seg.end, frames_per_window)
        if not frames:
            results.append((seg, 0.0))
            continue
        from PIL import Image

        images = [Image.fromarray(f) for f in frames]
        inputs = processor(
            text=texts, images=images,
            return_tensors="pt", padding=True, truncation=True,
        ).to(device)
        with torch.no_grad():
            img_f = net.get_image_features(pixel_values=inputs["pixel_values"])
            txt_f = net.get_text_features(
                input_ids=inputs["input_ids"],
                attention_mask=inputs.get("attention_mask"),
            )
        img_f = img_f / img_f.norm(dim=-1, keepdim=True)
        txt_f = txt_f / txt_f.norm(dim=-1, keepdim=True)
        sims = img_f @ txt_f.T  # (帧数, 文本数)
        score = float(sims.max().clamp(0.0, 1.0))
        results.append((seg, round(score, 4)))

    results.sort(key=lambda item: item[1], reverse=True)
    logger.info(
        "cv_clip_score: {} 窗口打分完成（model={} 最高 {:.3}）",
        len(results), model, results[0][1] if results else 0.0,
    )
    return results
