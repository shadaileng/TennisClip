"""球场关键点检测薄封装（方案 12 · Step 2.3，TennisCourtDetector 集成）。

模型契约（见 cv_runtime.load_torch_model 统一导出格式）：
- 输入：``(1, 3, H, W)`` RGB 归一化帧；
- 输出：14 个球场关键点 ``(14, 2)`` 像素坐标，可选第二输出 ``(3, 3)`` 单应矩阵。

球场是静止背景：按 interval 抽帧检测多次，取出现次数最多的量化结果（多数表决）
以抗瞬时误检；refine=True 时用 cv2.cornerSubPix 在灰度图上精修角点。
"""

from __future__ import annotations

from pathlib import Path

from app.utils.cv_runtime import CvUnavailable, load_torch_model, require, resolve_weights, resolve_device, ensure_device
from app.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_WEIGHTS = "court_detector.pth"
# 关键点数量约定（TennisCourtDetector 14 点）
KEYPOINT_COUNT = 14
# 多数表决的量化步长（像素）：落点相差 < QUANT 的检测视为同一点
_QUANT = 4.0


def _infer_points(model, torch, rgb_frame, device: str = "cpu"):
    """单帧推理 → (points[14][2], homography|None)；点数不符返回 None。

    契约：输出与输入同分辨率的像素坐标；模型在缩放分辨率上推理时
    自行还原（导出前接坐标还原层），本模块不再猜测缩放。
    """
    tensor = (
        torch.from_numpy(rgb_frame.copy())
        .permute(2, 0, 1).unsqueeze(0).float() / 255.0
        .to(device)
    )
    with torch.no_grad():
        out = model(tensor)
    if isinstance(out, (tuple, list)):
        pts = out[0]
        homo = out[1] if len(out) > 1 else None
    else:
        pts, homo = out, None
    pts = pts.detach().float().cpu().reshape(-1, 2).numpy()
    if len(pts) != KEYPOINT_COUNT:
        return None
    points = [[round(float(x), 2), round(float(y), 2)] for (x, y) in pts]
    homo_np = None
    if homo is not None:
        arr = homo.detach().float().cpu().numpy().reshape(-1)
        if arr.size == 9:
            homo_np = [[round(float(v), 6) for v in arr[i * 3:(i + 1) * 3]] for i in range(3)]
    return points, homo_np


def _refine(cv2, gray, points):
    """角点精修：以检测点为窗口中心，用 cornerSubPix 收敛到亚像素角点。"""
    import numpy as np

    refined = []
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.01)
    for (x, y) in points:
        p = np.array([[[float(x), float(y)]]], dtype=np.float32)
        try:
            cv2.cornerSubPix(gray, p, (7, 7), (-1, -1), criteria)
            refined.append([round(float(p[0][0][0]), 2), round(float(p[0][0][1]), 2)])
        except Exception:  # noqa: BLE001  非角点区域保持原检测值
            refined.append([x, y])
    return refined


def _majority_vote(detections: list) -> tuple | None:
    """多次检测的多数表决：量化后按点距离聚类，取最大簇的均值。"""
    if not detections:
        return None
    if len(detections) == 1:
        return detections[0]
    # 对每个点位分别投票，取每个索引上最常见量化值的均值
    voted: list = []
    for i in range(KEYPOINT_COUNT):
        buckets: dict = {}
        for pts, _h in detections:
            key = (round(pts[i][0] / _QUANT), round(pts[i][1] / _QUANT))
            buckets.setdefault(key, []).append(pts[i])
        best = max(buckets.values(), key=len)
        voted.append([
            round(sum(p[0] for p in best) / len(best), 2),
            round(sum(p[1] for p in best) / len(best), 2),
        ])
    # 单应矩阵取首个非 None
    homo = next((h for (_p, h) in detections if h is not None), None)
    return voted, homo


def detect_court(
    video_path: Path,
    refine: bool = True,
    interval: int = 30,
    weights: str = "",
    max_samples: int = 10,
    device: str = "auto",
) -> dict:
    """检测球场 14 关键点，返回 ``{"points": [[x,y]×14], "homography": 3×3|None}``。

    - interval：每隔多少帧采样一次（球场静止，无需逐帧）；
    - max_samples：采样次数上限（抗时长，0=不限）；
    - device：推理设备（auto=有 CUDA 用 CUDA 否则 CPU；cuda/cpu 显式）；
    - 依赖 torch/opencv、权重缺失 → 抛 CvUnavailable（节点 on_failure=skip 降级）。
    """
    torch = require("torch")
    cv2 = require("cv2")
    require("numpy")
    dev = ensure_device(device)
    model = load_torch_model(
        resolve_weights(
            DEFAULT_WEIGHTS, weights,
            hint="放置球场检测导出权重于 backend/data/models/court_detector.pth",
        ),
        device=dev,
    )

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise CvUnavailable(f"视频不可读：{video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    interval = max(1, int(interval))

    detections: list = []
    frame_no = 0
    last_frame = None
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_no % interval == 0:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                got = _infer_points(model, torch, rgb, dev)
                if got is not None:
                    detections.append(got)
                    last_frame = frame
                if max_samples and len(detections) >= max_samples:
                    break
            frame_no += 1
    finally:
        cap.release()

    voted = _majority_vote(detections)
    if voted is None:
        raise CvUnavailable(
            f"球场关键点检测无有效结果（采样 {frame_no} 帧，命中 {len(detections)} 次）"
        )
    points, homo = voted
    if refine and last_frame is not None:
        points = _refine(cv2, cv2.cvtColor(last_frame, cv2.COLOR_BGR2GRAY), points)

    logger.info(
        "cv_court: {} 关键点（interval={} 采样 {} 次 homography={}）",
        len(points), interval, len(detections), homo is not None,
    )
    return {"points": points, "homography": homo, "fps": round(fps, 2)}
