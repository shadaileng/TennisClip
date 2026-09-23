"""MediaPipe 姿态估计薄封装（方案 12 · Step 2.5）。

产出姿态关键点序列 dict（POSE 端口运行时类型）::

    {"fps": 采样帧率, "frames": [
        {"t": 秒, "people": [{"landmarks": [[x, y, z] × 33], "visibility": [0~1 × 33]}]},
        ...
    ]}

MediaPipe Pose 模型内置于 pip 包（无需外部权重，weights 参数仅保留契约对称）。
当前实现全帧单人主球员追踪；max_people 为多人扩展预留（>1 时按置信度截断）。
"""

from __future__ import annotations

from pathlib import Path

from app.utils.cv_runtime import CvUnavailable, require
from app.utils.logger import get_logger

logger = get_logger(__name__)

# 每帧最大人数（多人扩展预留，当前单人）
_MAX_STORED_PEOPLE = 1


def extract_poses(
    video_path: Path,
    max_people: int = 2,
    fps: float = 15.0,
    weights: str = "",
    max_frames: int = 0,
) -> dict:
    """按 fps 采样提取球员骨骼关键点序列，返回 POSE 端口 dict。

    - fps：采样帧率（按帧号步长近似，降计算量）；
    - 依赖 mediapipe/opencv → 抛 CvUnavailable（节点 on_failure=skip 降级）；
    - weights：MediaPipe 模型随包内置，此参数保留供自定义导出模型接入。
    """
    _ = weights  # 契约保留：当前 MediaPipe 模型内置于 pip 包
    require("mediapipe")
    cv2 = require("cv2")
    import mediapipe as mp  # require 成功后才导入

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise CvUnavailable(f"视频不可读：{video_path}")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    fps = min(max(1.0, float(fps)), src_fps)
    frame_stride = max(1, round(src_fps / fps))
    effective_fps = src_fps / frame_stride

    frames: list[dict] = []
    frame_no = 0
    people_limit = max(1, min(int(max_people), _MAX_STORED_PEOPLE))
    try:
        with mp.solutions.pose.Pose(
            static_image_mode=False, model_complexity=1,
            min_detection_confidence=0.5, min_tracking_confidence=0.5,
        ) as pose:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                if frame_no % frame_stride == 0:
                    res = pose.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    people: list[dict] = []
                    if res.pose_landmarks:
                        lms = res.pose_landmarks.landmark[: 33 * people_limit]
                        people.append({
                            "landmarks": [
                                [round(lm.x, 4), round(lm.y, 4), round(lm.z, 4)]
                                for lm in lms
                            ],
                            "visibility": [round(lm.visibility, 3) for lm in lms],
                        })
                    frames.append({
                        "t": round(frame_no / src_fps, 3),
                        "people": people,
                    })
                    if max_frames and len(frames) >= max_frames:
                        break
                frame_no += 1
    finally:
        cap.release()

    logger.info("cv_pose: {} 采样帧关键点（fps={} 帧/共 {} 帧）", len(frames), effective_fps, frame_no)
    return {"fps": round(effective_fps, 2), "frames": frames}
