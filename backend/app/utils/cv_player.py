"""球员检测追踪薄封装（方案 12 · Step 2.4，YOLOv8 集成）。

产出逐采样帧"运动分数"（帧内所有球员检测框中心位移总量，按画面对角线归一化），
由 ``event_detect.scores_to_candidates`` 复用掩码→窗口→合并逻辑聚合为候选窗口
（与旧运动信号同一套聚合，阈值语义一致）。

YOLOv8 权重（yolov8{n,s,m}.pt）首次使用由 ultralytics 自动下载；
离线环境请预放置权重文件到运行目录（或 Ultralytics 缓存目录）。
"""

from __future__ import annotations

from pathlib import Path

from app.utils.cv_runtime import CvUnavailable, require, ensure_device
from app.utils.logger import get_logger

logger = get_logger(__name__)

# COLO person class id
_PERSON_CLASS = 0
# 球员检测置信度下界（低于此值的框不参与位移统计）
_DET_CONF = 0.4
# YOLO 规格前缀映射
_MODEL_SIZES = {"n": "yolov8n.pt", "s": "yolov8s.pt", "m": "yolov8m.pt"}


def _greedy_displacement(prev_centers: list, centers: list, diag: float) -> float:
    """两帧球员中心的贪心最近匹配位移总量（按对角线归一化）。"""
    if not prev_centers or not centers or diag <= 0:
        return 0.0
    remaining = list(centers)
    total = 0.0
    for (px, py) in prev_centers:
        best_i, best_d = -1, None
        for i, (cx, cy) in enumerate(remaining):
            d = (px - cx) ** 2 + (py - cy) ** 2
            if best_d is None or d < best_d:
                best_i, best_d = i, d
        if best_i >= 0:
            total += best_d ** 0.5
            remaining.pop(best_i)
    return total / diag


def load_person_model(model_size: str = "n", weights: str = ""):
    """加载 YOLOv8 人体检测模型（供 compute_motion_scores / cv_visualize 复用）。

    - 权重本地缺失时由 ultralytics 自动下载，离线环境请预放置权重文件；
    - 依赖缺失/权重加载失败 → 抛 CvUnavailable（调用节点按 on_failure=skip 降级）；
    - 设备选择在预测时传入（detect_person_boxes 的 device 参数）。
    """
    require("ultralytics")
    weight_name = weights or _MODEL_SIZES.get(model_size, _MODEL_SIZES["n"])
    try:
        from ultralytics import YOLO  # require 成功后才导入
        return YOLO(weight_name)  # 本地缺失时自动下载，离线会抛错
    except Exception as exc:  # noqa: BLE001
        raise CvUnavailable(
            f"YOLOv8 权重 {weight_name} 加载失败（{exc}）；"
            f"请联网首次下载或预放置到运行目录"
        ) from exc


def detect_person_boxes(
    model, frame, device: str = "cpu", conf: float = _DET_CONF
) -> list[tuple[float, float, float, float, float]]:
    """单帧人体检测 → ``[(x1, y1, x2, y2, conf)]``（无检测返回空列表）。"""
    try:
        det = model.predict(
            frame, verbose=False, classes=[_PERSON_CLASS], conf=conf, device=device
        )[0]
    except Exception as exc:  # noqa: BLE001
        raise CvUnavailable(f"YOLOv8 推理失败：{exc}") from exc
    boxes = getattr(det, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return []
    xyxy = boxes.xyxy.cpu().numpy()
    confs = boxes.conf.cpu().numpy()
    return [
        (float(x1), float(y1), float(x2), float(y2), float(c))
        for (x1, y1, x2, y2), c in zip(xyxy, confs)
    ]


def compute_motion_scores(
    video_path: Path,
    duration: float,
    model_size: str = "n",
    sample_fps: float = 5.0,
    weights: str = "",
    max_frames: int = 0,
    device: str = "auto",
) -> tuple[list[float], float]:
    """YOLOv8 球员检测 + 贪心中心匹配，返回 ``(scores, step)``。

    - scores：逐采样帧运动分数（所有球员位移 / 画面对角线），无检测帧为 0；
    - step：采样步长（秒），即 ``1 / sample_fps``（按帧号近似）；
    - device：推理设备（auto=有 CUDA 用 CUDA 否则 CPU；cuda/cpu 显式）；
    - 依赖 ultralytics/torch/opencv → 抛 CvUnavailable（节点 on_failure=skip 降级）。
    """
    require("ultralytics")
    np = require("numpy")
    cv2 = require("cv2")

    # device 语义对齐 torch：auto=有 CUDA 用 CUDA 否则 CPU（ultralytics 默认即此行为，
    # 但显式传入保证跨版本一致 + cpu 可强制回退）
    dev = ensure_device(device)
    model = load_person_model(model_size=model_size, weights=weights)
    if duration <= 0:
        return [], 1.0

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise CvUnavailable(f"视频不可读：{video_path}")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_stride = max(1, round(src_fps / max(0.5, sample_fps)))
    step = frame_stride / src_fps

    scores: list[float] = []
    prev_centers: list = []
    diag = 1.0
    frame_no = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_no % frame_stride == 0:
                h, w = frame.shape[:2]
                diag = (w * w + h * h) ** 0.5
                det_boxes = detect_person_boxes(model, frame, device=dev)
                centers = [
                    ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
                    for (x1, y1, x2, y2, _c) in det_boxes
                ]
                scores.append(round(_greedy_displacement(prev_centers, centers, diag), 5))
                prev_centers = centers
                if max_frames and len(scores) >= max_frames:
                    break
            frame_no += 1
    finally:
        cap.release()

    logger.info(
        "cv_player: {} 采样帧运动分数（model={} stride={} 帧/共 {} 帧）",
        len(scores), model_size, frame_stride, frame_no,
    )
    return scores, step
