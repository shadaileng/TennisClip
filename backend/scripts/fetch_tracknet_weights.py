#!/usr/bin/env python3
"""TrackNet 权重一键获取与转换（方案 12 · CV 感知层辅助脚本）。

把公开渠道的 TrackNetV1 裸权重（state_dict）下载并转换为 ``cv_tracknet`` 可加载的
统一导出格式（TorchScript），落盘 ``backend/data/models/tracknet.pth``。

用法（backend/ 目录）::

    uv run python scripts/fetch_tracknet_weights.py                 # 默认 HuggingFace 源：下载 + 转换 + 冒烟
    uv run python scripts/fetch_tracknet_weights.py --from-file X   # 转换已下载的本地权重
    uv run python scripts/fetch_tracknet_weights.py --url <URL>     # 从任意直链下载后转换

前提：``uv sync --extra cv``（脚本需要 torch）。
HuggingFace 直连不畅时可 ``export HF_ENDPOINT=https://hf-mirror.com`` 后重试。

转换时自动做「节点契约适配」（包一层 ``TrackNetHeatmap`` 再 ``torch.jit.script`` 导出）：

1. **通道反序 [8..0]**：本项目 ``cv_tracknet`` 输入为 (前前,前,当前)×RGB 栈，
   而公开实现（yastrebksv/TrackNet）训练用 (当前,前,前前)×BGR 栈——
   时间反序与 R/B 交换恰好复合为整段通道反转，一步对齐；
2. **非 8 倍数分辨率**：右/下补零推理后裁回原尺寸（节点按原始帧分辨率入参，
   热图尺寸 ≠ 帧尺寸时 ``_peak_to_point`` 也会等比映射）；
3. **输出语义**：``1 − P(背景类)`` 单通道「球存在概率」热图 (B,1,H,W)，
   与节点峰值取点契约一致（对标签取值 {0,1} / {0,255} / 高斯分布均鲁棒）。

处理流程：获取源文件 → 格式识别（TorchScript / state_dict / 整模 pickle）→
装入内置 TrackNetV1 参考结构（严格对位校验）→ 契约适配层导出 TorchScript →
落盘前冒烟（含补零分支）→ 经 ``cv_runtime.load_torch_model`` 二次确认。
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.request
import warnings
from pathlib import Path

# torch.jit 处于维护模式的弃用提示与本脚本无关（项目加载契约即 TorchScript），降噪
warnings.filterwarnings("ignore", message=r"`torch\.jit\.\w+` is deprecated")

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 默认源：HuggingFace（vishnushenoy09/tracknet-v1-tennis，TrackNetV1 三帧输入权重，
# 改编自 yastrebksv/TrackNet；README 声明「probability heatmap of the ball position」）
HF_REPO = "vishnushenoy09/tracknet-v1-tennis"
HF_FILE = "tracknet_weights.pth"

DEFAULT_OUT = BACKEND_DIR / "data" / "models" / "tracknet.pth"


class FetchError(Exception):
    """脚本失败：main 捕获后打印指引并退出码 1。"""


def _require_torch():
    try:
        import torch
        import torch.nn as nn
        import torch.nn.functional as F
    except ImportError as exc:  # pragma: no cover - 环境缺失时的指引
        raise FetchError(
            "缺少 torch：请先在 backend/ 下执行 `uv sync --extra cv` 再重试"
        ) from exc
    return torch, nn, F


# ---------------------------------------------------------------------------
# 内置参考结构（层布局对齐 yastrebksv/TrackNet 的 BallTrackerNet，独立重写；
# 仅用于 state_dict 对位装载，forward 语义由 TrackNetHeatmap 统一接管）
# ---------------------------------------------------------------------------

def _build_classes(nn, torch, F):
    """构造参考结构与契约适配层（依赖 torch 已导入，延迟到运行时定义类）。"""

    class _ConvBlock(nn.Module):
        """Conv3x3(bias) → ReLU → BatchNorm2d，参数键 convN.block.{0,2}.*。"""

        def __init__(self, cin: int, cout: int) -> None:
            super().__init__()
            self.block = nn.Sequential(
                nn.Conv2d(cin, cout, kernel_size=3, padding=1, bias=True),
                nn.ReLU(),
                nn.BatchNorm2d(cout),
            )

        def forward(self, x):
            return self.block(x)

    class TrackNetV1(nn.Module):
        """TrackNetV1 参考结构（256 类逐像素热图 logits，输出与输入同分辨率）。"""

        def __init__(self, out_channels: int = 256) -> None:
            super().__init__()
            self.conv1 = _ConvBlock(9, 64)
            self.conv2 = _ConvBlock(64, 64)
            self.pool1 = nn.MaxPool2d(2, 2)
            self.conv3 = _ConvBlock(64, 128)
            self.conv4 = _ConvBlock(128, 128)
            self.pool2 = nn.MaxPool2d(2, 2)
            self.conv5 = _ConvBlock(128, 256)
            self.conv6 = _ConvBlock(256, 256)
            self.conv7 = _ConvBlock(256, 256)
            self.pool3 = nn.MaxPool2d(2, 2)
            self.conv8 = _ConvBlock(256, 512)
            self.conv9 = _ConvBlock(512, 512)
            self.conv10 = _ConvBlock(512, 512)
            self.ups1 = nn.Upsample(scale_factor=2)
            self.conv11 = _ConvBlock(512, 256)
            self.conv12 = _ConvBlock(256, 256)
            self.conv13 = _ConvBlock(256, 256)
            self.ups2 = nn.Upsample(scale_factor=2)
            self.conv14 = _ConvBlock(256, 128)
            self.conv15 = _ConvBlock(128, 128)
            self.ups3 = nn.Upsample(scale_factor=2)
            self.conv16 = _ConvBlock(128, 64)
            self.conv17 = _ConvBlock(64, 64)
            self.conv18 = _ConvBlock(64, out_channels)

        def forward(self, x):
            x = self.conv1(x)
            x = self.conv2(x)
            x = self.pool1(x)
            x = self.conv3(x)
            x = self.conv4(x)
            x = self.pool2(x)
            x = self.conv5(x)
            x = self.conv6(x)
            x = self.conv7(x)
            x = self.pool3(x)
            x = self.conv8(x)
            x = self.conv9(x)
            x = self.conv10(x)
            x = self.ups1(x)
            x = self.conv11(x)
            x = self.conv12(x)
            x = self.conv13(x)
            x = self.ups2(x)
            x = self.conv14(x)
            x = self.conv15(x)
            x = self.ups3(x)
            x = self.conv16(x)
            x = self.conv17(x)
            return self.conv18(x)

    class TrackNetHeatmap(nn.Module):
        """节点契约适配层：通道反序 → 补零对齐 8 倍数 → 推理 → 球概率热图 → 裁回。

        输入 (B,9,H,W) float 0~1（节点栈：前前,前,当前 × RGB）；
        输出 (B,1,H,W) ∈ [0,1]（1 − P(背景类) = 球存在概率）。
        """

        def __init__(self, backbone: nn.Module) -> None:
            super().__init__()
            self.backbone = backbone
            # [8..0]：节点 (前前,前,当前)×RGB → 训练栈 (当前,前,前前)×BGR
            self.register_buffer("reorder", torch.arange(8, -1, -1))

        def forward(self, x):
            x = torch.index_select(x, 1, self.reorder)
            h = int(x.shape[2])
            w = int(x.shape[3])
            # 补零步长 8（3 次池化 / 3 次上采样）；字面量便于 TorchScript 编译
            pad_multiple = 8
            ph = (pad_multiple - h % pad_multiple) % pad_multiple
            pw = (pad_multiple - w % pad_multiple) % pad_multiple
            if ph != 0 or pw != 0:
                x = F.pad(x, (0, pw, 0, ph))
            logits = self.backbone(x)  # (B, 256, H', W')
            prob = 1.0 - torch.softmax(logits, dim=1)[:, :1]
            if ph != 0 or pw != 0:
                prob = prob[:, :, :h, :w]
            return prob

    return TrackNetV1, TrackNetHeatmap


# ---------------------------------------------------------------------------
# 源文件获取
# ---------------------------------------------------------------------------

def _download(url: str, dest: Path) -> None:
    base = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
    if "huggingface.co" in url and base != "https://huggingface.co":
        url = url.replace("https://huggingface.co", base)
    print(f"  GET {url}")
    req = urllib.request.Request(
        url, headers={"User-Agent": "tennisclip-fetch-weights/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, dest.open("wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            next_report = 20 * 1024 * 1024
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if total and done >= next_report:
                    print(f"  已下载 {done // (1 << 20)} / {total // (1 << 20)} MB")
                    next_report += 20 * 1024 * 1024
    except OSError as exc:
        raise FetchError(
            f"下载失败：{exc}\n"
            "  网络受限时可 `export HF_ENDPOINT=https://hf-mirror.com` 后重试，"
            "或手动下载后用 --from-file 传入"
        ) from exc
    print(f"  完成：{dest}（{dest.stat().st_size // (1 << 20)} MB）")


# ---------------------------------------------------------------------------
# 格式识别 → state_dict
# ---------------------------------------------------------------------------

def _extract_state_dict(torch, path: Path) -> dict:
    """把 TorchScript / 裸 state_dict / 整模 pickle / 包装 dict 统一成 state_dict。"""
    # 1) TorchScript：取其 state_dict 走统一转换（忽略原 forward 语义，本脚本重新定义）
    try:
        scripted = torch.jit.load(str(path))
        sd = dict(scripted.state_dict())
        if sd:
            print(f"  识别为 TorchScript（{len(sd)} 键），取 state_dict 转换")
            return sd
    except Exception:
        pass

    # 2) torch.load：先走安全模式（裸 state_dict 足够），失败再回退完整反序列化
    last_err: Exception | None = None
    for weights_only, tag in ((True, "安全模式"), (False, "完整反序列化")):
        try:
            obj = torch.load(str(path), map_location="cpu", weights_only=weights_only)
        except ModuleNotFoundError as exc:
            raise FetchError(
                f"整模 pickle 引用了源仓库代码（{exc}）：\n"
                "  请在源仓库环境中导出裸 state_dict 或 TorchScript 后，用 --from-file 传入"
            ) from exc
        except Exception as exc:
            last_err = exc
            if weights_only:
                print(f"  安全模式加载失败（{type(exc).__name__}），回退完整反序列化…")
            continue

        if isinstance(obj, dict) and obj:
            if all(torch.is_tensor(v) for v in obj.values()):
                print(f"  识别为裸 state_dict（{len(obj)} 键）")
                return dict(obj)
            for key in ("state_dict", "model_state_dict", "model", "net", "weights"):
                inner = obj.get(key)
                if isinstance(inner, dict) and inner and all(
                    torch.is_tensor(v) for v in inner.values()
                ):
                    print(f"  识别为包装 dict['{key}']（{len(inner)} 键）")
                    return dict(inner)
            raise FetchError(
                "文件是 dict 但不含张量 state_dict，"
                f"顶层键：{list(obj.keys())[:8]}——请从源仓库重新导出"
            )
        if isinstance(obj, torch.nn.Module):
            sd = dict(obj.state_dict())
            print(f"  识别为完整模型 pickle（state_dict {len(sd)} 键）")
            return sd
        raise FetchError(f"无法识别的对象类型：{type(obj).__name__}")

    raise FetchError(f"反序列化失败：{last_err}")


# ---------------------------------------------------------------------------
# 转换与冒烟
# ---------------------------------------------------------------------------

def _convert(torch, nn, F, source: Path, out: Path) -> None:
    TrackNetV1, TrackNetHeatmap = _build_classes(nn, torch, F)

    sd = _extract_state_dict(torch, source)
    model = TrackNetV1()
    try:
        missing, unexpected = model.load_state_dict(sd, strict=False)
    except RuntimeError as exc:
        raise FetchError(
            "权重键名与内置 TrackNetV1 结构对不上（形状不匹配）：\n"
            f"  {str(exc).splitlines()[0]}\n"
            "  内置结构对齐 yastrebksv/TrackNet（HF 默认源可用）；其他版本"
            "（V2/V3/V4 等）请用其源仓库代码导出 TorchScript 后 --from-file 传入"
        ) from exc
    if missing or unexpected:
        detail = []
        if missing:
            detail.append(f"缺失 {len(missing)}：{missing[:6]}")
        if unexpected:
            detail.append(f"多余 {len(unexpected)}：{unexpected[:6]}")
        raise FetchError(
            "state_dict 与内置 TrackNetV1 结构不对位：\n  "
            + "\n  ".join(detail)
            + "\n  非 TrackNetV1 结构请改用源仓库导出的 TorchScript（--from-file）"
        )
    print(f"  对位装载成功（{len(sd)} 键，参数 "
          f"{sum(p.numel() for p in model.parameters()) / 1e6:.1f}M）")

    net = TrackNetHeatmap(model).eval()
    print("  torch.jit.script 导出中…")
    try:
        scripted = torch.jit.script(net)
    except Exception as exc:
        raise FetchError(f"torch.jit.script 失败：{exc}") from exc

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    scripted.save(str(tmp))

    # 落盘前冒烟：常规分辨率 + 补零分支（非 8 倍数）
    print("  冒烟前向：360x640 与 361x641…")
    with torch.no_grad():
        reloaded = torch.jit.load(str(tmp))
        y1 = reloaded(torch.rand(1, 9, 360, 640))
        y2 = reloaded(torch.rand(1, 9, 361, 641))
    for y, expect in ((y1, (1, 1, 360, 640)), (y2, (1, 1, 361, 641))):
        if tuple(y.shape) != expect:
            tmp.unlink(missing_ok=True)
            raise FetchError(f"输出形状 {tuple(y.shape)} ≠ 期望 {expect}")
        if not bool(torch.isfinite(y).all()) or float(y.min()) < -1e-6 or float(y.max()) > 1 + 1e-6:
            tmp.unlink(missing_ok=True)
            raise FetchError("输出含非有限值或超出 [0,1] 值域")
    tmp.replace(out)
    print(f"  已写入 {out}（{out.stat().st_size // 1024} KB）")


def _smoke_via_app(out: Path) -> None:
    """经项目内统一加载器二次确认（与 detect.tracknet 节点同一条加载路径）。"""
    try:
        from app.utils.cv_runtime import load_torch_model
    except Exception as exc:
        raise FetchError(f"导入 app.utils.cv_runtime 失败（backend 环境异常）：{exc}") from exc
    try:
        load_torch_model(out)
    except Exception as exc:
        raise FetchError(f"cv_runtime.load_torch_model 拒绝该文件：{exc}") from exc
    print("  cv_runtime.load_torch_model 通过（与节点加载路径一致）")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="下载并转换 TrackNet 权重为 cv_tracknet 可加载的 TorchScript"
    )
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--url", help="从任意直链下载权重文件")
    src.add_argument("--from-file", help="使用已下载的本地权重文件，跳过下载")
    parser.add_argument(
        "--out", default=str(DEFAULT_OUT),
        help=f"导出路径（默认 {DEFAULT_OUT}）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    torch, nn, F = _require_torch()

    out = Path(args.out).expanduser().resolve()
    workdir = out.parent / ".fetch_tmp"
    try:
        # [1/3] 获取源文件
        if args.from_file:
            source = Path(args.from_file).expanduser().resolve()
            if not source.is_file():
                raise FetchError(f"--from-file 不存在：{source}")
            print(f"[1/3] 使用本地文件 {source}")
        else:
            workdir.mkdir(parents=True, exist_ok=True)
            if args.url:
                url = args.url
            else:
                url = (
                    "https://huggingface.co/"
                    f"{HF_REPO}/resolve/main/{HF_FILE}"
                )
            source = workdir / "source_weights"
            print(f"[1/3] 下载源权重（{args.url or f'默认 HF 源 {HF_REPO}'}）")
            _download(url, source)

        # [2/3] 识别 + 装载 + 契约适配导出
        print(f"[2/3] 转换 → {out}")
        _convert(torch, nn, F, source, out)

        # [3/3] 项目加载路径二次确认
        print("[3/3] 校验项目加载路径")
        _smoke_via_app(out)
    except FetchError as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1
    finally:
        if workdir.exists():
            for stale in workdir.iterdir():
                stale.unlink(missing_ok=True)
            workdir.rmdir()

    print(
        "\n完成。下一步：\n"
        "  1) 激活内置「CV 增强工作流」（见 docs/guides/01-CV增强工作流使用说明.md 第三节）；\n"
        "  2) 上传视频验证 detect.tracknet 状态为 done（该指南第四节三层确认）。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
