"""TrackNet 网球追踪薄封装（方案 12 · Step 2.2）。

模型契约（统一导出格式，见 cv_runtime.load_torch_model）：
- 输入：三连 RGB 帧堆叠张量 ``(1, 9, H, W)``（H/W 为原帧尺寸），float，0~1 归一化；
- 输出：球位置热图（任意分辨率），元素为球存在概率——峰值坐标按热图/帧尺寸等比映射回帧坐标。
推理后取热图峰值，峰值概率 ≥ confidence 才保留为轨迹点。

职责边界：本模块只产出原始轨迹点 ``{"t", "x", "y", "confidence"}``；
聚合成候选窗口由 ``event_detect.track_to_candidates`` 完成（与旧信号共用聚合逻辑）。
"""

from __future__ import annotations

from pathlib import Path

from app.utils.cv_runtime import CvUnavailable, load_torch_model, require, resolve_weights
from app.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_WEIGHTS = "tracknet.pth"
# 三连帧缓冲初始不足时跳过的起始帧（保证输入恒为 3 帧）
_WARMUP_FRAMES = 2


def _peak_to_point(heatmap, out_h: int, out_w: int, conf_thr: float):
    """热图 → 单点 (x, y, p)；峰值低于阈值返回 None。坐标映射回原始分辨率。"""
    hm = heatmap
    while getattr(hm, "dim", lambda: 0)() > 2:
        hm = hm[0]
    hm = hm.detach().float().cpu()
    flat_idx = int(hm.argmax())
    h, w = hm.shape[-2], hm.shape[-1]
    y, x = divmod(flat_idx, w)
    p = float(hm.reshape(-1)[flat_idx])
    if p < conf_thr:
        return None
    # 热图坐标 → 帧坐标（模型可在缩放分辨率上推理，热图尺寸 ≠ 帧尺寸时等比映射）
    return (round(x * out_w / w, 2), round(y * out_h / h, 2), round(p, 4))


def track_video(
    video_path: Path,
    duration: float,
    confidence: float = 0.5,
    frame_stride: int = 1,
    weights: str = "",
    max_frames: int = 0,
) -> list[dict]:
    """逐帧推理网球位置，返回按时间升序的轨迹点列表。

    - frame_stride：每 N 帧取 1 帧（降 GPU 负载），时间戳按真实帧号换算；
    - max_frames：调试用帧数上限（0=不限）；
    - 依赖 torch/opencv、权重缺失 → 抛 CvUnavailable（节点 on_failure=skip 降级）。
    """
    torch = require("torch")
    np = require("numpy")
    cv2 = require("cv2")
    model = load_torch_model(resolve_weights(
        DEFAULT_WEIGHTS, weights,
        hint="放置 TrackNet 导出权重于 backend/data/models/tracknet.pth",
    ))
    if duration <= 0:
        return []

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise CvUnavailable(f"视频不可读：{video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    points: list[dict] = []
    buffer: list = []
    frame_no = 0
    sampled = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_no % max(1, frame_stride) == 0:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                buffer.append(rgb)
                if len(buffer) >= _WARMUP_FRAMES + 1:
                    t = frame_no / fps
                    stack = np.concatenate(buffer[-3:], axis=-1)  # (H, W, 9)
                    tensor = (
                        torch.from_numpy(stack.copy())
                        .permute(2, 0, 1).unsqueeze(0).float() / 255.0
                    )
                    with torch.no_grad():
                        out = model(tensor)
                    if isinstance(out, (tuple, list)):
                        out = out[0]
                    h, w = frame.shape[:2]
                    point = _peak_to_point(out, h, w, confidence)
                    if point:
                        points.append({
                            "t": round(t, 3), "x": point[0],
                            "y": point[1], "confidence": point[2],
                        })
                sampled += 1
                if max_frames and sampled >= max_frames:
                    break
            frame_no += 1
    finally:
        cap.release()

    logger.info(
        "cv_tracknet: {} 轨迹点（stride={} 采样 {} 帧 / 共 {} 帧）",
        len(points), frame_stride, sampled, frame_no,
    )
    return points
