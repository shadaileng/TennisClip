"""轨迹可视化绘制薄封装（诊断 TrackNet 检出情况）。

把球轨迹（时间窗折线 + 当前帧最近检出点）与球员检测框叠加到视频帧，
并绘制 HUD 统计（轨迹点数 / 检出覆盖率 / tracklet 分段）与底部检出时间线
（绿=该时段有球检出、红=无检出、白线=播放头），产出诊断视频
``track_overlay.mp4``，供人工判断「网球是否被识别 / 捡球段是否混入」。

多球区分：TrackNet 每帧仅取热图 argmax 单点，场上多球时 argmax 会帧间跳变——
本模块按物理连续性把点序列切成 tracklet（时间间隔 + 隐含速度断线），
段间断线、段配色轮换，静止段（地上的球）灰色标注、命中标记带段号。

职责边界：纯渲染——不产生候选、不修改任何上游值。
依赖 cv2（dev/cv 组）；球员框依赖 ultralytics（cv 组）。
缺失/失败抛 ``CvUnavailable`` → 节点 ``on_failure="skip"`` 级联降级。
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from pathlib import Path

from app.utils import cv_player
from app.utils.cv_runtime import CvUnavailable, require, ensure_device
from app.utils.logger import get_logger

logger = get_logger(__name__)

# 颜色（BGR）
_C_BALL_OK = (0, 220, 0)      # 当前帧检出且置信度达标：绿
_C_BALL_LOW = (0, 165, 255)   # 检出但低置信：橙
_C_MISS = (0, 0, 255)         # 未检出提示：红
_C_TRAIL = (255, 255, 0)      # 轨迹折线基准色（多球分段配色第 1 色）
_C_STATIC = (170, 170, 170)   # 静止球段（地上的球）：灰
_C_PLAYER = (255, 180, 40)    # 球员框：黄
_C_HUD_BG = (0, 0, 0)         # HUD 面板底
_C_BIN_HIT = (0, 200, 0)      # 时间线：有检出
_C_BIN_MISS = (40, 40, 200)   # 时间线：无检出
_C_PLAYHEAD = (255, 255, 255)  # 时间线：播放头

# tracklet 段配色轮换（BGR）：青 / 品红 / 黄 / 蓝
_SEG_COLORS = [_C_TRAIL, (255, 0, 255), (0, 255, 255), (255, 128, 0)]

_DEFAULT_DURATION_BIN = 0.5   # 时间线分桶粒度（秒）
_BREAK_GAP_DEFAULT = 0.5      # 多球分段：相邻点时间间隔断线阈值（秒）
_MAX_SPEED_DEFAULT = 10000.0  # 多球分段：隐含速度断线阈值（px/s）


# ---------- 纯辅助函数（无 cv2 依赖，可单测） ----------

def points_in_window(times: list[float], points: list[dict], t0: float, t1: float) -> list[dict]:
    """返回轨迹时间戳落在 [t0, t1] 的点（times 与 points 一一对应且按 t 升序）。"""
    lo = bisect_left(times, t0)
    hi = bisect_right(times, t1)
    return points[lo:hi]


def nearest_point(times: list[float], points: list[dict], t: float, tol: float = 0.2):
    """返回 t 之前最近且时间差 ≤ tol 的轨迹点；无命中返回 None。

    只向前找（不取未来点）：避免用「下一帧才检出」的点提前标记当前帧。
    """
    hi = bisect_right(times, t)
    if hi == 0:
        return None
    p = points[hi - 1]
    return p if (t - p["t"]) <= tol else None


def coverage_bins(times: list[float], duration: float, bin_seconds: float = _DEFAULT_DURATION_BIN) -> list[bool]:
    """按固定时长分桶统计检出覆盖：桶内任一轨迹点即为 True。

    duration ≤ 0 或无轨迹返回空列表（调用方跳过时间线绘制）。
    """
    if duration <= 0 or not times:
        return []
    n = max(1, math.ceil(duration / bin_seconds))
    flags = [False] * n
    for t in times:
        i = min(n - 1, max(0, int(t / bin_seconds)))
        flags[i] = True
    return flags


def split_tracklets(
    points: list[dict],
    break_gap: float = _BREAK_GAP_DEFAULT,
    max_speed: float = _MAX_SPEED_DEFAULT,
) -> list[list[dict]]:
    """按物理连续性把按 t 升序的轨迹点切成 tracklet 段（多球区分）。

    断线规则（任一命中即开新段）：

    - 相邻点时间间隔 > ``break_gap``（漏检久，连线无意义）；
    - 隐含速度 dist/Δt > ``max_speed``（帧间跳到另一颗球——场上多球时
      argmax 单点在球间跳变的典型特征）；
    - Δt == 0 且位置不同（同一时刻的异球点）必断。

    连续稠密点保持同段；单点自成一段；空输入返回 []。纯函数、不改输入。
    """
    if not points:
        return []
    segments: list[list[dict]] = []
    cur: list[dict] = [points[0]]
    for prev, p in zip(points, points[1:]):
        dt = float(p["t"]) - float(prev["t"])
        dist = math.hypot(
            float(p["x"]) - float(prev["x"]), float(p["y"]) - float(prev["y"])
        )
        if dt > break_gap:
            speed = math.inf      # 间隔过大：断
        elif dt > 0:
            speed = dist / dt
        else:
            speed = 0.0 if dist == 0 else math.inf  # 同刻异点：断
        if speed > max_speed:
            segments.append(cur)
            cur = [p]
        else:
            cur.append(p)
    segments.append(cur)
    return segments


def is_static_segment(points: list[dict], radius: float = 10.0, min_span: float = 3.0) -> bool:
    """段内点全部落在首点半径 ``radius`` 内且时间跨度 ≥ ``min_span`` → 静止球（地上的球）。

    比赛中的球必然移动；长时间原地不动的检出多半是场上停留的
    备用球/捡球，标注为灰色供人工区分。单点（span=0）不算静止。
    """
    if len(points) < 2:
        return False
    if float(points[-1]["t"]) - float(points[0]["t"]) < min_span:
        return False
    x0, y0 = float(points[0]["x"]), float(points[0]["y"])
    return all(
        math.hypot(float(p["x"]) - x0, float(p["y"]) - y0) <= radius for p in points
    )


# ---------- 绘制（cv2 按需导入） ----------

def _draw_trail(cv2, frame, window, hit, ball_conf, seg_by_id=None, static_segs=None):
    """绘制轨迹窗口（多球分段折线）+ 当前帧最近检出点（hit，可为 None）。

    - window：``[(p, seg_id), ...]`` 按 t 升序；仅**同段**相邻点连线，
      段间自然断线、段配色轮换（青/品红/黄/蓝），静止段灰色——
      多球时不同球的点分属不同 tracklet，视觉上不再串成一条线；
    - hit 命中标记带段号（``ball#seg 置信度``），静止段灰标 ``static?``。
    """
    h, w = frame.shape[:2]
    r = max(6, min(w, h) // 100)
    static_segs = static_segs or set()
    prev = None
    prev_seg = None
    for p, seg in window:
        cur = (int(p["x"]), int(p["y"]))
        if prev is not None and seg == prev_seg:
            color = _C_STATIC if seg in static_segs else _SEG_COLORS[seg % len(_SEG_COLORS)]
            cv2.line(frame, prev, cur, color, 2, cv2.LINE_AA)
        prev, prev_seg = cur, seg
    if hit is None:
        return
    seg = (seg_by_id or {}).get(id(hit))
    center = (int(hit["x"]), int(hit["y"]))
    if seg is not None and seg in static_segs:
        color, tag = _C_STATIC, f"ball#{seg} static?"
    else:
        color = _C_BALL_OK if float(hit.get("confidence", 0)) >= ball_conf else _C_BALL_LOW
        conf = hit.get("confidence", 0)
        tag = f"ball#{seg} {conf:.2f}" if seg is not None else f"ball {conf:.2f}"
    cv2.circle(frame, center, r, color, 2, cv2.LINE_AA)
    cv2.circle(frame, center, max(2, r // 3), color, -1, cv2.LINE_AA)
    cv2.putText(
        frame, tag,
        (center[0] + r + 4, center[1] - 4),
        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA,
    )


def _draw_boxes(cv2, frame, boxes: list[tuple]):
    """绘制球员检测框 [(x1, y1, x2, y2, conf)]。"""
    for (x1, y1, x2, y2, conf) in boxes:
        p1, p2 = (int(x1), int(y1)), (int(x2), int(y2))
        cv2.rectangle(frame, p1, p2, _C_PLAYER, 2, cv2.LINE_AA)
        cv2.putText(
            frame, f"person {conf:.2f}",
            (p1[0], max(14, p1[1] - 6)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, _C_PLAYER, 1, cv2.LINE_AA,
        )


def _draw_hud(cv2, frame, lines: list[str]):
    """左上角半透明 HUD 面板。"""
    h, w = frame.shape[:2]
    panel_w = min(w, max(360, int(w * 0.45)))
    panel_h = 14 + 20 * len(lines)
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (panel_w, panel_h), _C_HUD_BG, -1)
    cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)
    for i, text in enumerate(lines):
        cv2.putText(
            frame, text, (8, 22 + 20 * i),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (240, 240, 240), 1, cv2.LINE_AA,
        )


def _draw_timeline(cv2, frame, flags: list[bool], duration: float, t: float):
    """底部检出时间线：绿=有球检出 / 红=无 / 白线=播放头。"""
    if not flags or duration <= 0:
        return
    h, w = frame.shape[:2]
    bar_h = max(8, h // 60)
    y0, y1 = h - bar_h, h
    n = len(flags)
    for i, hit in enumerate(flags):
        x0 = int(i * w / n)
        x1 = max(x0 + 1, int((i + 1) * w / n))
        cv2.rectangle(frame, (x0, y0), (x1, y1), _C_BIN_HIT if hit else _C_BIN_MISS, -1)
    px = min(w - 1, max(0, int(t / duration * w)))
    cv2.line(frame, (px, y0 - 2), (px, y1), _C_PLAYHEAD, 2, cv2.LINE_AA)


# ---------- 主入口 ----------

def render_track_overlay(
    video_path: Path,
    track: list[dict],
    out_path: Path,
    *,
    duration: float = 0.0,
    show_players: bool = True,
    trail_seconds: float = 2.0,
    ball_conf: float = 0.5,
    player_stride: int = 3,
    model_size: str = "n",
    device: str = "auto",
    break_gap: float = _BREAK_GAP_DEFAULT,
    max_speed: float = _MAX_SPEED_DEFAULT,
    task_id: str = "",
) -> Path:
    """逐帧渲染球轨迹 + 球员框叠加视频，返回输出文件路径。

    - track：``[{"t", "x", "y", "confidence"}]``（detect.tracknet 输出，可为空列表——
      空轨迹仍渲染：全红时间线正是「球未被识别」的诊断证据）；
    - duration：时间线总时长；≤0 时回退为帧数/帧率推算；
    - trail_seconds：轨迹回看窗口（当前帧往前该秒数内的点连成折线）；
    - ball_conf：当前帧标记点的达标置信度（达标绿、不达标橙）；
    - break_gap / max_speed：多球分段规则——相邻点间隔超此秒数、
      或隐含速度超此 px/s 即断线换段（见 split_tracklets）；
    - player_stride：每 N 帧跑一次球员检测，中间帧复用最近一次框（降负载）；
    - task_id：协作式取消检查点——每帧前检测旗标，命中抛 TaskCancelled；
    - 依赖 cv2 / ultralytics（show_players=True 时）→ 缺失抛 CvUnavailable。
    """
    cv2 = require("cv2")
    points = sorted(
        [p for p in (track or []) if isinstance(p.get("t"), (int, float)) and p.get("t") is not None],
        key=lambda p: p["t"],
    )
    times = [float(p["t"]) for p in points]
    n_conf = sum(1 for p in points if float(p.get("confidence", 0)) >= ball_conf)

    # 多球区分：按物理连续性切 tracklet，点 → 段号映射（绘制用，不改上游值）
    segments = split_tracklets(points, break_gap=break_gap, max_speed=max_speed)
    seg_by_id: dict[int, int] = {}
    for i, seg in enumerate(segments):
        for p in seg:
            seg_by_id[id(p)] = i

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise CvUnavailable(f"视频不可读：{video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if duration <= 0 and frame_count > 0 and fps > 0:
        duration = frame_count / fps
    flags = coverage_bins(times, duration)

    # 静止段判定（地上的球）：半径随分辨率缩放（720p≈10px）
    static_radius = max(10.0, min(w, h) / 72.0)
    static_segs = {
        i for i, seg in enumerate(segments)
        if is_static_segment(seg, radius=static_radius)
    }

    # 球员框依赖 ultralytics：显式先加载，缺依赖立即抛（不留到循环内反复失败）
    person_model = None
    if show_players:
        person_model = cv_player.load_person_model(model_size=model_size)
        dev = ensure_device(device)
    else:
        dev = "cpu"

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h)
    )
    if not writer.isOpened():
        cap.release()
        raise CvUnavailable(f"无法创建输出视频（mp4v 编码不可用）：{out_path}")

    marker_tol = max(0.2, 3.0 / fps)  # 最近检出点容忍窗（覆盖 stride 采样间隔）
    stride = max(1, int(player_stride))
    boxes: list[tuple] = []
    frames = 0
    n_miss = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            t = frames / fps if fps > 0 else 0.0
            if task_id:
                from app.utils import cancel
                cancel.check_cancelled(task_id, f"轨迹可视化帧 {frames}")

            if show_players and frames % stride == 0:
                boxes = cv_player.detect_person_boxes(person_model, frame, device=dev)

            window = [
                (p, seg_by_id[id(p)])
                for p in points_in_window(times, points, max(0.0, t - trail_seconds), t)
            ]
            hit = nearest_point(times, points, t, marker_tol)
            if hit is None:
                n_miss += 1
            _draw_trail(cv2, frame, window, hit, ball_conf, seg_by_id, static_segs)
            if show_players:
                _draw_boxes(cv2, frame, boxes)

            cov = (sum(flags) / len(flags) * 100.0) if flags else 0.0
            _draw_hud(cv2, frame, [
                f"t={t:.1f}s  ball pts={len(points)} (conf>={ball_conf:.2f}: {n_conf})",
                f"coverage={cov:.0f}%  persons={len(boxes) if show_players else 0}"
                + ("" if show_players else " (players off)"),
                f"tracklets={len(segments)} static={len(static_segs)}"
                + f" | split: dt>{break_gap:.1f}s v>{max_speed:.0f}px/s",
            ])
            if hit is None and times and t >= times[0]:
                # 轨迹已开始但仍无命中 → 显式提示「当前帧未检出球」
                h_img, w_img = frame.shape[:2]
                cv2.putText(
                    frame, "NO BALL DETECTED",
                    (w_img - 300, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, _C_MISS, 2, cv2.LINE_AA,
                )
            _draw_timeline(cv2, frame, flags, duration, t)

            writer.write(frame)
            frames += 1
    finally:
        cap.release()
        writer.release()

    cov = (sum(flags) / len(flags) * 100.0) if flags else 0.0
    logger.info(
        "cv_visualize: 输出 {}（{} 帧，球轨迹 {} 点 → {} 段（静止 {}），检出覆盖率 {:.1}%，未检出帧 {}）",
        out_path, frames, len(points), len(segments), len(static_segs), cov, n_miss,
    )
    return out_path
