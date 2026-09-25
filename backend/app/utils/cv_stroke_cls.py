"""击球类型分类薄封装（方案 12 · Step 2.7）。

两种分类器（节点 ParamSpec ``classifier`` 选择）：

- ``dtw``：姿态动态时间规整。对高光段内的球员关键点序列做重采样 + 平移/缩放
  归一化，与模板库（JSON）逐标签求 DTW 距离，``conf = 1 / (1 + 归一化距离)``；
  纯 numpy/Python，无需 GPU。模板缺失抛 CvUnavailable。
- ``cnn``：3D CNN 直接对段内采样帧分类（导出权重契约同 cv_runtime），
  模型可带 ``classes`` 属性（缺省用 DEFAULT_LABELS 八类）。

输出 ``{段下标: (label, conf)}``；低于置信度阈值的覆盖决策在节点侧执行
（min_confidence 是节点参数，保持单一真相源）。
"""

from __future__ import annotations

from pathlib import Path

from app.models import Segment
from app.utils.cv_runtime import (
    CvUnavailable,
    load_torch_model,
    require,
    resolve_device,
    resolve_weights,
    sample_frames,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_LABELS = (
    "forehand", "backhand", "serve", "volley",
    "smash", "forehand_slice", "backhand_slice", "dropshot",
)
DEFAULT_TEMPLATES = "stroke_templates.json"
DEFAULT_WEIGHTS = "stroke_cnn.pth"
# DTW 查询/模板统一重采样长度（帧）——兼顾判别力与 Python DP 开销
_SEQ_LEN = 16
# 段内至少要有多少有效姿态帧才参与 dtw 分类
_MIN_POSE_FRAMES = 4
# CNN 输入时长（帧数）与空间尺寸
_CNN_FRAMES = 16
_CNN_SIZE = (112, 112)


# ---------- 序列处理（纯 Python，可直接单测） ----------


def dtw_distance(a: list, b: list) -> float:
    """经典 DTW 距离（欧氏步进，O(n·m) DP）。序列元素为等长数值向量。"""
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return float("inf")
    prev = [0.0] * (m + 1)
    prev[0] = 0.0
    for j in range(1, m + 1):
        prev[j] = float("inf")
    for i in range(1, n + 1):
        cur = [0.0] * (m + 1)
        cur[0] = float("inf")
        ai = a[i - 1]
        for j in range(1, m + 1):
            bj = b[j - 1]
            d = sum((x - y) ** 2 for x, y in zip(ai, bj)) ** 0.5
            cur[j] = d + min(prev[j], cur[j - 1], prev[j - 1])
        prev = cur
    return prev[m]


def _resample(seq: list, target: int = _SEQ_LEN) -> list:
    """线性插值重采样到固定帧数（seq 为空返回空）。"""
    n = len(seq)
    if n == 0:
        return []
    if n == target:
        return [list(f) for f in seq]
    out = []
    for i in range(target):
        pos = i * (n - 1) / max(target - 1, 1)
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        frac = pos - lo
        out.append([
            v0 + (v1 - v0) * frac for v0, v1 in zip(seq[lo], seq[hi])
        ])
    return out


def _normalize(seq: list) -> list:
    """时间均值去平移 + 最大模长归一化（平移/缩放不变，旋转不敏感）。"""
    if not seq:
        return []
    dim = len(seq[0])
    mean = [sum(f[d] for f in seq) / len(seq) for d in range(dim)]
    centered = [[f[d] - mean[d] for d in range(dim)] for f in seq]
    scale = max(
        (sum(v * v for v in f) ** 0.5) for f in centered
    )
    if scale <= 1e-9:
        return centered
    return [[v / scale for v in f] for f in centered]


def _prepare(seq: list) -> list:
    return _normalize(_resample(seq))


# ---------- 姿态 → 查询序列 ----------


def _segment_pose_frames(pose: dict, seg: Segment) -> list:
    """从 POSE dict 取段内主球员逐帧特征（33 关键点 × x,y 展平）。"""
    if not pose:
        return []
    frames = pose.get("frames") or []
    seq = []
    for fr in frames:
        t = float(fr.get("t", -1.0))
        if t < seg.start - 1e-6 or t > seg.end + 1e-6:
            continue
        people = fr.get("people") or []
        if not people:
            continue
        lms = people[0].get("landmarks") or []
        if len(lms) < 10:  # 关键点残缺帧丢弃
            continue
        seq.append([float(pt[0]) for pt in lms] + [float(pt[1]) for pt in lms])
    return seq


# ---------- dtw 分类 ----------


def _load_templates(path: Path) -> dict:
    """读取姿态模板库。

    JSON 格式：``{"<label>": [序列, ...]}``，序列 = 帧列表，帧 = ``[[x, y, z], ...]``
    ——与 detect.pose 输出的 landmarks 同构（帧内逐点展平 x,y 与查询序列对齐）。
    """
    import json

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise CvUnavailable(f"姿态模板库读取失败（{path}）：{exc}") from exc
    templates: dict[str, list] = {}
    for label, seqs in (data or {}).items():
        prepared = []
        for seq in seqs or []:
            feats = []
            valid = True
            for frame in seq:
                flat = []
                for p in frame:
                    if not isinstance(p, (list, tuple)) or len(p) < 2:
                        valid = False
                        break
                    flat += [float(p[0]), float(p[1])]
                if not valid:
                    break
                if flat:
                    feats.append(flat)
            if valid and feats:
                prepared.append(_prepare(feats))
        if prepared:
            templates[str(label)] = prepared
    if not templates:
        raise CvUnavailable(f"姿态模板库为空或格式不合法（{path}），无法做 dtw 分类")
    return templates


def _classify_dtw(segments: list[Segment], pose: dict, weights: str) -> dict:
    if pose is None:
        raise ValueError(
            "击球分类器 dtw 需要姿态输入（请连线 detect.pose 的 pose 输出，"
            "或改用 cnn 分类器）"
        )
    templates = _load_templates(resolve_weights(
        DEFAULT_TEMPLATES, weights,
        hint="放置姿态模板库于 backend/data/models/stroke_templates.json",
    ))
    preds: dict = {}
    for idx, seg in enumerate(segments):
        query = _prepare(_segment_pose_frames(pose, seg))
        if len(query) < _MIN_POSE_FRAMES:
            continue
        best_label, best_conf = "", 0.0
        for label, seqs in templates.items():
            for tpl in seqs:
                dist = dtw_distance(query, tpl)
                dn = dist / max(len(query), len(tpl), 1)
                conf = 1.0 / (1.0 + dn)
                if conf > best_conf:
                    best_label, best_conf = label, conf
        if best_label:
            preds[idx] = (best_label, round(best_conf, 4))
    return preds


# ---------- cnn 分类 ----------


def _classify_cnn(video_path: Path, segments: list[Segment], weights: str, device: str = "auto") -> dict:
    torch = require("torch")
    np = require("numpy")
    dev = resolve_device(device)
    model = load_torch_model(
        resolve_weights(
            DEFAULT_WEIGHTS, weights,
            hint="放置 3D CNN 导出权重于 backend/data/models/stroke_cnn.pth",
        ),
        device=dev,
    )
    classes = [str(c) for c in getattr(model, "classes", DEFAULT_LABELS)]
    preds: dict = {}
    for idx, seg in enumerate(segments):
        frames = sample_frames(video_path, seg.start, seg.end, _CNN_FRAMES, size=_CNN_SIZE)
        if not frames:
            continue
        arr = np.stack([np.array(f, dtype=np.uint8) for f in frames])  # (T, H, W, C)
        tensor = (
            torch.from_numpy(arr).permute(3, 0, 1, 2).unsqueeze(0).float() / 255.0
            .to(dev)
        )  # (B=1, C, T, H, W)
        with torch.no_grad():
            out = model(tensor)
        logits = out.logits if hasattr(out, "logits") else out
        probs = torch.softmax(logits.reshape(-1), dim=-1)
        conf, pred = probs.max(0)
        i = int(pred)
        if i < len(classes):
            preds[idx] = (classes[i], round(float(conf), 4))
    return preds


def classify_segments(
    video_path: Path,
    segments: list[Segment],
    pose: dict | None = None,
    classifier: str = "cnn",
    weights: str = "",
    device: str = "auto",
) -> dict[int, tuple[str, float]]:
    """对各高光段分类击球类型，返回 ``{段下标: (label, confidence)}``。

    - dtw：需 pose 输入与模板库；cnn：需 torch 与导出权重；
    - device：cnn 推理设备（auto=有 CUDA 用 CUDA 否则 CPU；cuda/cpu 显式）；
    - 依赖/权重缺失 → 抛 CvUnavailable；dtw 缺 pose → 抛 ValueError（可修复的连线问题）。
    """
    if classifier == "dtw":
        preds = _classify_dtw(segments, pose, weights)
    elif classifier == "cnn":
        preds = _classify_cnn(video_path, segments, weights, device)
    else:
        raise ValueError(f"未知击球分类器：{classifier}，应为 cnn / dtw")
    logger.info("cv_stroke_cls: 分类 {}/{} 段（classifier={} device={}）", len(preds), len(segments), classifier, device)
    return preds
