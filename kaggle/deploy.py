#!/usr/bin/env python3
"""Kaggle GPU 验证一键部署脚本（Python 标准库，无第三方依赖，只调 kaggle CLI）。

把「CV 增强调试工作流」完整移植到 Kaggle 免费 GPU 的整条链路压成一条命令：

    检查凭据 → 组装 dataset（调 build_dataset.ps1）→ kaggle datasets create
    → 推 notebook（kaggle kernels push）→ 打印「Kaggle 侧要做什么 + 完成后怎么拉结果」

设计边界：
- 本脚本**不做** GPU 推理本身（那是 Kaggle 侧 notebook 跑 verify.py 的活），
  只负责「把代码/权重/视频推上去 + 把 notebook 推上去」这一段。
- 拉取结果（kaggle kernels pull）是运行完之后的动作，脚本末尾打印命令供用户执行，
  不自动 pull（Kaggle 免费 GPU 运行 10 分钟 ~ 2 小时，脚本跑完时 notebook 还没跑完）。
- kaggle CLI 必须已安装且已配凭据（~/.kaggle/kaggle.json 或 $KAGGLE_USER_NAME/$KAGGLE_API_KEY）；
  脚本只做「能跑通吗」预检，不代装 CLI。

用法：
    python kaggle/deploy.py                # 全流程（默认）
    python kaggle/deploy.py --skip-upload   # 只组装本地 dataset 目录，不上传 Kaggle
    python kaggle/deploy.py --slug my-slug # 指定 dataset slug（默认 kaggle-tennisclip-verify）

退出码：0=成功；1=失败（缺 CLI/缺凭据/上传失败/无视频权重等）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent          # kaggle/
BACKEND = ROOT.parent / "backend"
DATASET = ROOT / "dataset"
SLUG_DEFAULT = "kaggle-tennisclip-verify"
KERNEL_SLUG_DEFAULT = "kaggle-tennisclip-verify"   # notebook slug


def sh(cmd: list[str], cwd: Path | None = None, dry: bool = False) -> tuple[int, str, str]:
    """跑外部命令，返回 (exit_code, stdout, stderr)。dry=True 只打印不执行。"""
    if dry:
        print(f"  [dry] {' '.join(str(c) for c in cmd)}")
        return (0, "", "")
    try:
        r = subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None,
                           capture_output=True, text=True, timeout=600)
        return (r.returncode, r.stdout, r.stderr)
    except FileNotFoundError:
        return (127, "", "命令不存在：" + cmd[0])
    except subprocess.TimeoutExpired:
        return (124, "", "超时")


def step(n: int, total: int, title: str) -> None:
    print(f"\n[{n}/{total}] {title}")
    print("-" * 46)


def check_kaggle_cli() -> bool:
    """kaggle CLI 是否可用（pip 装的 kaggle 包提供 kaggle 命令）。"""
    code, out, err = sh(["kaggle", "--version"])
    if code != 0:
        print("  ✗ 未找到 kaggle CLI（" + (err.strip() or out.strip() or "命令不存在") + "）")
        print("  请先：pip install kaggle   并配凭据：")
        print("    Windows: mkdir $HOME\\.kaggle  把 kaggle.json 放里面（kaggle.com→账户→Create New API Token）")
        print("    或设环境变量 KAGGLE_USER_NAME / KAGGLE_API_KEY")
        return False
    print(f"  ✓ kaggle CLI {out.strip()}")
    return True


def check_credentials() -> bool:
    """凭据预检：能调 kaggle api 说明配好；不强制（上传时才真正用到）。"""
    has_env = bool(os.environ.get("KAGGLE_USER_NAME") and os.environ.get("KAGGLE_API_KEY"))
    has_file = Path.home().joinpath(".kaggle", "kaggle.json").exists()
    if has_env or has_file:
        print("  ✓ 凭据来源：" + ("环境变量 KAGGLE_USER_NAME/KAGGLE_API_KEY" if has_env
                                  else "文件 ~/.kaggle/kaggle.json"))
        return True
    print("  ⚠ 未检测到 kaggle 凭据（无环境变量、无 ~/.kaggle/kaggle.json）")
    print("    上传步骤会失败。先配凭据再重跑，或先 --skip-upload 只组装本地。")
    return False


def build_dataset() -> bool:
    """调 build_dataset.ps1 组装 kaggle/dataset/（含 verify.py/app.tar.gz/权重/视频/meta.json）。"""
    ps1 = ROOT / "build_dataset.ps1"
    if not ps1.is_file():
        print(f"  ✗ 缺 {ps1}")
        return False
    # Windows 用 powershell；跨平台退回 sh -c 调 pwsh（Kaggle 验证主场景是 Windows 本机）
    if os.name == "nt":
        code, out, err = sh(["powershell", "-NoProfile", "-File", str(ps1)], cwd=ROOT)
    else:
        code, out, err = sh(["pwsh", "-NoProfile", "-File", str(ps1)], cwd=ROOT)
    if out and out.strip():
        print(out)
    if err and err.strip():
        print(err)
    if code != 0:
        print(f"  ✗ build_dataset.ps1 退出码 {code}")
        return False
    print(f"  ✓ dataset 已组装：{DATASET}")
    return True


def verify_dataset_content() -> bool:
    """组装产物完整性：meta.json + verify.py + app 包 + (权重可选) + (视频可选)。"""
    ok = True
    if not DATASET.joinpath("meta.json").is_file():
        print("  ✗ 缺 meta.json"); ok = False
    if not DATASET.joinpath("verify.py").is_file():
        print("  ✗ 缺 verify.py"); ok = False
    if not (DATASET.joinpath("app.tar.gz").is_file() or DATASET.joinpath("app.zip").is_file()):
        print("  ✗ 缺 app.tar.gz / app.zip（app 代码包）"); ok = False
    vids = list(DATASET.glob("data/*.mp4")) if DATASET.joinpath("data").is_dir() else []
    pths = list(DATASET.glob("weights/*.pth")) if DATASET.joinpath("weights").is_dir() else []
    if not vids:
        print("  ⚠ data/ 下无 mp4 视频——Kaggle 跑起来会因「无视频」退出。")
        print("     先往 kaggle/dataset/data/ 放 1-5 分钟网球视频再重跑（或 backend/data/sample_videos/）")
        # 无视频不算致命（可先传代码再补视频），但提示
    if not pths:
        print("  ⚠ weights/ 下无 .pth 权重——Kaggle 上 TrackNet 会 CvUnavailable 级联跳过（验证链路仍通）。")
        print("     要真跑推理，先 backend/scripts/fetch_tracknet_weights.py 生成 tracknet.pth 放入。")
    if ok:
        print(f"  ✓ dataset 内容就绪（视频 {len(vids)} 个 / 权重 {len(pths)} 个）")
    return ok


def upload_dataset(slug: str) -> bool:
    """kaggle datasets create -p dataset（dataset/ 根须有 meta.json）。"""
    print(f"  上传 dataset → {slug}")
    code, out, err = sh(["kaggle", "datasets", "create", "-p", "dataset"], cwd=ROOT)
    if out and out.strip():
        print(out)
    if err and err.strip():
        print(err)
    if code != 0:
        print(f"  ✗ 上传失败（退出码 {code}）")
        print("     常见原因：凭据缺失/5GB 超限/slug 被占用。改 slug 重传：")
        print(f"     1) 编辑 {DATASET / 'meta.json'} 的 dataset_name 或传参 --slug")
        print(f"     2) 重跑：python {ROOT / 'deploy.py'} --slug <新slug>")
        return False
    print(f"  ✓ dataset 已上传 Kaggle：{slug}")
    return True


def push_notebook(kernel_slug: str) -> bool:
    """kaggle kernels push -p . 把 kaggle_verify.ipynb 推成 Kaggle notebook。"""
    ipynb = ROOT / "kaggle_verify.ipynb"
    if not ipynb.is_file():
        print(f"  ✗ 缺 notebook 模板 {ipynb}")
        return False
    print(f"  推送 notebook → {kernel_slug}")
    code, out, err = sh(["kaggle", "kernels", "push", "-p", "."], cwd=ROOT)
    if out and out.strip():
        print(out)
    if code != 0:
        print(f"  ✗ notebook 推送失败（退出码 {code}）：{(err or '').strip()[:400]}")
        return False
    print(f"  ✓ notebook 已推送")
    print(f"    在 https://www.kaggle.com/kernels 搜 '{kernel_slug}' 找到")
    return True


def print_manual_steps(slug: str, kernel_slug: str) -> None:
    """Kaggle 侧人工步骤 + 完成后拉结果命令。"""
    print("\n" + "=" * 60)
    print("下一步（人工，Kaggle 网页）：")
    print("=" * 60)
    print(f"  1) 打开 https://www.kaggle.com/kernels 搜 '{kernel_slug}'（或你的用户名下）")
    print(f"     Settings → Input Data 勾 '{slug}'")
    print(f"     Settings → Accelerator 选 GPU T4 16GB / P100")
    print(f"     Settings → Internet 勾 ON（CLIP 需下载 HF 权重）")
    print(f"     点 Run All 运行（单条视频 1-5 分钟不会触 9h 上限）")
    print(f"\n  2) 运行完成后（OUTPUT 区出现 track_overlay.mp4）拉结果：")
    print(f"     kaggle kernels pull <你的用户名>/{kernel_slug} -p out/")
    print(f"     或网页直接下载 /kaggle/working/ 下的产物")
    print(f"\n  3) 人工核对（对齐方案 14 §2.4）：")
    print(f"     out/ 下各 kaggle-XX/track_overlay.mp4 + result.json + summary.json")
    print(f"     - G1：金标回合内球轨迹是否连续（红未检出=漏检）")
    print(f"     - G2：静止段（灰标）是否落在回合外；与回合重叠=死球误入")
    print(f"     - 多球跳变：调 post.visualize_track 的 max_speed/break_gap 重跑")


def _safe_stdout() -> None:
    """Windows 控制台 GBK 无法打印 ✓/✗/中文——强制 stdout/stderr 切 UTF-8（PEP 540）。

    失败（如流已被重定向且不可重配）则静默降级，不阻断脚本。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main() -> int:
    _safe_stdout()
    ap = argparse.ArgumentParser(description="Kaggle GPU 验证一键部署（标准库，调 kaggle CLI）")
    ap.add_argument("--slug", default=SLUG_DEFAULT, help="dataset slug")
    ap.add_argument("--kernel-slug", default=KERNEL_SLUG_DEFAULT, help="notebook slug")
    ap.add_argument("--skip-upload", action="store_true",
                    help="只组装本地 dataset 目录，不上传/不推 notebook")
    ap.add_argument("--allow-missing-cli", action="store_true",
                    help="缺 kaggle CLI 时不阻断（--skip-upload 场景：组装本地 dataset 不依赖 CLI）")
    args = ap.parse_args()

    # 全程步骤计数（CLI/凭据/组装/完整性/上传/推notebook/人工步骤 = 7；skip-upload 时只到 4）
    step_no = 0
    total = 4 if args.skip_upload else 7
    print("=" * 60)
    print("Kaggle GPU 验证 · 一键部署（方案 14 · Step 2/6）")
    print("=" * 60)

    def next_step(title: str) -> None:
        nonlocal step_no
        step_no += 1
        step(step_no, total, title)

    # 1) CLI 预检
    next_step("kaggle CLI 预检")
    if not check_kaggle_cli():
        if args.allow_missing_cli:
            print("  （--allow-missing-cli：跳过阻断，继续本地组装；上传步骤仍会失败）")
        else:
            print("\n终止：缺 kaggle CLI。先 pip install kaggle 配凭据再重跑。")
            return 1

    # 2) 凭据预检
    next_step("Kaggle 凭据预检")
    has_cred = check_credentials()
    if not has_cred and not args.skip_upload:
        print("\n终止：无凭据无法上传。配凭据（~/.kaggle/kaggle.json 或环境变量）再重跑，")
        print("      或用 --skip-upload 只组装本地 dataset 目录。")
        return 1

    # 3) 组装 dataset
    next_step("组装 kaggle/dataset/（build_dataset.ps1）")
    if not build_dataset():
        print("\n终止：dataset 组装失败。检查 build_dataset.ps1 报错。")
        return 1

    # 4) 内容完整性
    next_step("dataset 内容完整性")
    verify_dataset_content()

    if args.skip_upload:
        print("\n[完成] --skip-upload：只组装本地 dataset，未上传。")
        print(f"       手动上传：kaggle datasets create -p {DATASET}")
        return 0

    # 5) 上传 dataset
    next_step("上传 Kaggle dataset（kaggle datasets create）")
    if not upload_dataset(args.slug):
        print("\n终止：dataset 上传失败。")
        return 1

    # 6) 推 notebook
    next_step("推送 Kaggle notebook（kaggle kernels push）")
    if not push_notebook(args.kernel_slug):
        print("\n终止：notebook 推送失败。")
        return 1

    # 7) 人工步骤
    next_step("Kaggle 侧人工步骤 + 结果拉取")
    print_manual_steps(args.slug, args.kernel_slug)
    print(f"\n[完成] 部署链路已跑通。GPU 推理在 Kaggle 侧 notebook 里执行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
