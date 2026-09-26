#!/usr/bin/env python3
"""Kaggle GPU 验证一键部署脚本（Python 标准库，无第三方依赖，只调 kaggle CLI）。

把「CV 增强调试工作流」完整移植到 Kaggle 免费 GPU 的整条链路压成一条命令：

    检查凭据 → 纯 Python 组装 dataset（shutil/tarfile，无 shell 依赖）→ kaggle datasets create
    → 推 notebook（kaggle kernels push）→ 打印「Kaggle 侧要做什么 + 完成后怎么拉结果」

设计边界：
- 本脚本**不做** GPU 推理本身（那是 Kaggle 侧 notebook 跑 verify.py 的活），
  只负责「把代码/权重/视频推上去 + 把 notebook 推上去」这一段。
- 拉取结果（kaggle kernels pull）是运行完之后的动作，脚本末尾打印命令供用户执行，
  不自动 pull（Kaggle 免费 GPU 运行 10 分钟 ~ 2 小时，脚本跑完时 notebook 还没跑完）。
- kaggle CLI 必须已安装且已配凭据。新版 CLI（≥1.6）推荐用 OAuth access token：
  `kaggle auth login`（浏览器授权，token 缓存到 ~/.kaggle/access_token）或写
  `kaggle/.env` 的 `KAGGLE_API_TOKEN=你的token`；旧版 `KAGGLE_USER_NAME`/`KAGGLE_API_KEY`
  + `~/.kaggle/kaggle.json` 已逐步弃用（CLI 2.x 默认不再读取 kaggle.json）。
  脚本只做「能跑通吗」预检，不代装 CLI、不代跑 OAuth 流程。
- kaggle CLI 2.x 的 `datasets create -p <folder>` 只上传 **folder 根下的平铺文件**
  （默认 dir_mode=skip，子目录不上传），且认新版 `dataset-metadata.json`（旧 meta.json 已不读）。
  故 deploy.py 组装 dataset/ 为平铺布局（视频 dataset_sample.mp4、权重 dataset_weights/*.pth），
  上传命令固定 `kaggle datasets create -p dataset`（相对 kaggle/ 根），勿手改 -p 指向。
- dataset 组装用纯 Python 标准库（shutil/tarfile/json）实现，**不依赖任何 shell 脚本**
  （bash / PowerShell / tar CLI 都不需要），在 bash（Linux/macOS/Git Bash）或 Windows
  原生 PowerShell 里 `python deploy.py` 行为完全一致。
  build_dataset.ps1 保留供习惯 PowerShell 的用户单独使用，deploy.py 不再调用它。

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
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent          # kaggle/
BACKEND = ROOT.parent / "backend"
DATASET = ROOT / "dataset"
SLUG_DEFAULT = "kaggle-tennisclip-verify"
KERNEL_SLUG_DEFAULT = "kaggle-tennisclip-verify"   # notebook slug
DOTENV_FILE = ROOT / ".env"                          # kaggle/.env（KAGGLE_USER_NAME / KAGGLE_API_KEY，.gitignore 忽略）
DOTENV_NAME = "kaggle/.env"


def load_dotenv() -> dict:
    """解析 kaggle/.env（KEY=VALUE，# 注释/空行跳过），返回 dict。标准库手写，零依赖。

    文件不存在/无有效键时返回空 dict。进程环境变量优先（不覆盖已有值），
    缺失的 KAGGLE_USER_NAME / KAGGLE_API_KEY 由 .env 补齐。
    兼容 BOM 前缀（PowerShell 5.1 的 Set-Content -Encoding UTF8 / Out-File 会写 BOM）。
    """
    values: dict = {}
    if not DOTENV_FILE.is_file():
        return values
    try:
        raw = DOTENV_FILE.read_bytes()
        # 剥离 UTF-8 BOM（PowerShell 5.1 写文件带 BOM 会导致首行 key 前缀错乱）
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        lines = raw.decode("utf-8", errors="replace").splitlines()
    except OSError:
        return values
    for ln in lines:
        line = ln.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'\"")
        if key:
            values[key] = val
    return values


def kaggle_env() -> dict:
    """kaggle CLI 子进程环境：进程环境变量 + kaggle/.env 补齐（环境变量优先）。

    新版 kaggle CLI（≥1.6）认：
      - KAGGLE_API_TOKEN（OAuth access token，或 kaggle auth login 缓存）
      - 旧版 KAGGLE_USER_NAME / KAGGLE_API_KEY + ~/.kaggle/kaggle.json（逐步弃用）
    这里把 .env 的值注进子进程 env，使脚本在「.env 配 token、机器无 access_token」时也能上传。
    """
    env = dict(os.environ)
    file_values = load_dotenv()
    for k, v in file_values.items():
        env.setdefault(k, v)   # 环境变量优先，.env 只补缺
    return env


def sh(cmd: list[str], cwd: Path | None = None, dry: bool = False, env: dict | None = None) -> tuple[int, str, str]:
    """跑外部命令，返回 (exit_code, stdout, stderr)。dry=True 只打印不执行。

    env：子进程环境变量（缺省继承 os.environ；kaggle CLI 命令传 kaggle_env() 注入 .env 凭据）。
    """
    if dry:
        print(f"  [dry] {' '.join(str(c) for c in cmd)}")
        return (0, "", "")
    try:
        r = subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None,
                           capture_output=True, text=True, timeout=600, env=env)
        return (r.returncode, r.stdout, r.stderr)
    except FileNotFoundError as e:
        missing = getattr(e, "filename") or str(cmd[0])
        return (127, "", f"命令不存在：{missing}")
    except subprocess.TimeoutExpired:
        return (124, "", "超时")


def step(n: int, total: int, title: str) -> None:
    print(f"\n[{n}/{total}] {title}")
    print("-" * 46)


def check_kaggle_cli() -> bool:
    """kaggle CLI 是否可用（pip 装的 kaggle 包提供 kaggle 命令）。"""
    code, out, err = sh(["kaggle", "--version"])
    if code != 0:
        print("  [FAIL] 未找到 kaggle CLI（" + (err.strip() or out.strip() or "命令不存在") + "）")
        print("  请先：pip install kaggle   再配凭据：")
        print("    新版（推荐）：kaggle auth login（浏览器授权，token 缓存 ~/.kaggle/access_token）")
        print("    或写 kaggle/.env：KAGGLE_API_TOKEN=你的token")
        print("    旧版：KAGGLE_USER_NAME + KAGGLE_API_KEY + ~/.kaggle/kaggle.json")
        return False
    print(f"  [ok] kaggle CLI {out.strip()}")
    return True


def check_credentials() -> bool:
    """凭据预检（新版 kaggle CLI）：识别以下任一来源——
       1) 环境变量 KAGGLE_API_TOKEN（新版 OAuth access token）
       2) ~/.kaggle/access_token 文件（kaggle auth login 后生成）
       3) 环境变量 KAGGLE_USER_NAME+KAGGLE_API_KEY（旧版，逐步弃用）
       4) kaggle/.env 文件内任一上述键
       5) ~/.kaggle/kaggle.json（旧版 user:pass，CLI 2.x 默认不再读取）

    预检只判断「有无可识别凭据」；上传时 deploy.py 子进程注入 kaggle_env()。
    """
    home = Path.home()
    has_token_env = bool(os.environ.get("KAGGLE_API_TOKEN"))
    has_token_file = (home / ".kaggle" / "access_token").is_file()
    has_old_env = bool(os.environ.get("KAGGLE_USER_NAME") and os.environ.get("KAGGLE_API_KEY"))
    file_values = load_dotenv()
    has_dotenv_token = bool(file_values.get("KAGGLE_API_TOKEN"))
    has_dotenv_old = bool(file_values.get("KAGGLE_USER_NAME"))
    has_kaggle_json = (home / ".kaggle" / "kaggle.json").is_file()

    if has_token_env:
        print("  [ok] 凭据来源：环境变量 KAGGLE_API_TOKEN（新版）")
        return True
    if has_token_file:
        print(f"  [ok] 凭据来源：{home / '.kaggle' / 'access_token'}（kaggle auth login 缓存）")
        return True
    if has_dotenv_token:
        print(f"  [ok] 凭据来源：{DOTENV_FILE.name} 的 KAGGLE_API_TOKEN（将注入 kaggle CLI 子进程）")
        return True
    if has_old_env:
        print("  [ok] 凭据来源：环境变量 KAGGLE_USER_NAME/KAGGLE_API_KEY（旧版）")
        return True
    if has_dotenv_old:
        print(f"  [ok] 凭据来源：{DOTENV_FILE.name} 的 KAGGLE_USER_NAME/KAGGLE_API_KEY（旧版）")
        return True
    if has_kaggle_json:
        print(f"  [ok] 凭据来源：{home / '.kaggle' / 'kaggle.json'}（旧版；kaggle CLI 2.x 可能已不再读取，建议改 token）")
        return True

    print("  [warn] 未检测到 kaggle 凭据（无 KAGGLE_API_TOKEN、无 access_token、无旧版 user/key）")
    print(f"    推荐（新版 CLI）三选一：")
    print(f"      A. 运行一次：kaggle auth login   （浏览器授权，token 缓存到 ~/.kaggle/access_token）")
    print(f"      B. 写 {DOTENV_NAME}（.gitignore 已忽略不入库）：")
    print(f"         KAGGLE_API_TOKEN=你的token")
    print(f"         （kaggle.com → Settings → API → Generate New Token）")
    print(f"      C. 设环境变量 KAGGLE_API_TOKEN")
    print(f"    旧版（逐步弃用）：KAGGLE_USER_NAME + KAGGLE_API_KEY + ~/.kaggle/kaggle.json")
    print("    上传步骤会失败。先配凭据再重跑，或先 --skip-upload 只组装本地。")
    return False


def build_dataset(slug: str = SLUG_DEFAULT) -> bool:
    """纯 Python 组装 kaggle/dataset/（平铺布局，kaggle CLI 2.x 默认 dir_mode=skip 可全量上传）：
    dataset-metadata.json（新版 schema）+ verify.py + app.tar.gz + 根下 mp4 + dataset_weights/。

    全用 Python 标准库（shutil/tarfile/os）实现，不依赖 powershell / bash / tar CLI——
    在 bash（Linux/macOS/Git Bash）或 Windows 原生 PowerShell 里 `python deploy.py` 行为一致。
    build_dataset.ps1 保留供习惯 PowerShell 的用户单独使用（旧 data/weights 布局）。
    """
    # 0. 清理旧产物（只重建本次要生成的文件；用户手动放的其它 mp4 不动）
    for old in (DATASET / "app.tar.gz", DATASET / "app.zip", DATASET / "meta.json"):
        old.unlink(missing_ok=True)

    # 1. 校验前置
    if not (BACKEND / "app").is_dir():
        print(f"  [FAIL] 缺 {BACKEND / 'app'}，请确认在仓库内运行")
        return False
    verify_py = ROOT / "verify.py"
    if not verify_py.is_file():
        print(f"  [FAIL] 缺 {verify_py}")
        return False

    # 1b. 依赖检查（Kaggle notebook 会 pip 装；本地 dry-run 需 backend/.venv 已 uv sync）
    deps = ["loguru", "pydantic", "yaml", "dotenv", "torch", "cv2", "ultralytics", "transformers"]
    missing = []
    for d in deps:
        r = sh([sys.executable, "-c", f"import {d}"])
        if r[0] != 0:
            missing.append(d)
    if missing:
        print(f"  [warn] 以下依赖当前 python 未装（Kaggle 侧由 notebook cell 1 pip 安装；本地 dry-run 用 backend/.venv 即可）：{', '.join(missing)}")

    # 2. dataset-metadata.json（kaggle CLI 2.x 新版 schema：id/title/licenses；
    #    旧版 meta.json 的 dataset_name/license 字段 2.x 已不读取）
    # 注意：kaggle CLI 用 json.load(open(...)) 读 metadata（系统默认编码，中文 Windows=GBK），
    #       故 metadata 全字段必须 ASCII——中文会 UnicodeDecodeError 导致上传失败。
    DATASET.mkdir(parents=True, exist_ok=True)
    owner = _kaggle_username() or "YOUR_USERNAME"
    title = "TennisClip CV verify (code+weights+video)"   # 6-50 字符
    meta_content = {
        "id": f"{owner}/{slug}",
        "title": title,
        "licenses": [{"name": "CC0-1.0"}],
        "description": "TennisClip AI - tennis video highlight detection + CV verification "
                       "(16-node executor graph + TrackNet ball tracking + trajectory overlay). "
                       "Run verify.py on Kaggle GPU via kaggle_verify.ipynb.",
        "keywords": ["computer-vision", "tennis", "ball-tracking"],
    }
    (DATASET / "dataset-metadata.json").write_text(json.dumps(meta_content, indent=2), encoding="ascii")
    print(f"  [1/4] dataset-metadata.json 已生成（id={owner}/{slug}；"
          + ("owner 为占位 YOUR_USERNAME，请编辑 dataset/dataset-metadata.json 的 id 为 <你的Kaggle用户名>/" + slug + " 再上传" if owner == "YOUR_USERNAME" else "已从本地 kaggle 配置解出") + "）")

    # 3. 拷贝 verify.py
    shutil.copy2(verify_py, DATASET / "verify.py")
    print("  [2/4] verify.py 已拷贝")

    # 4. 打包 backend/{app,prompts} → dataset/app.tar.gz
    # report 节点 import prompts.highlight_analysis（顶层包），verify.py 的 --repo 指向解压根（含 app/ 与 prompts/）
    tar_path = DATASET / "app.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        for sub in ("app", "prompts"):
            src_dir = BACKEND / sub
            if src_dir.is_dir():
                tf.add(src_dir, arcname=sub)
    print(f"  [3/4] app.tar.gz 已生成（tarfile，含 app/ + prompts/）：{tar_path}")

    # 5. 视频与权重（kaggle CLI 2.x 默认 dir_mode=skip 子目录不上传，须平铺在 dataset/ 根）
    src_videos = BACKEND / "data" / "sample_videos"
    src_models = BACKEND / "data" / "models"

    if src_videos.is_dir():
        mp4s = sorted(src_videos.glob("*.mp4"))
        if mp4s:
            shutil.copy2(mp4s[0], DATASET / "dataset_sample.mp4")
            extra = f"（忽略其余 {len(mp4s) - 1} 个，多视频请手动拷到 dataset/ 根下，文件名避开 dataset_sample.mp4）" if len(mp4s) > 1 else ""
            print(f"  [4/4] 视频已平铺：{mp4s[0].name} → dataset_sample.mp4{extra}")
        else:
            print("  [warn] backend/data/sample_videos/ 下无 mp4（Kaggle 侧会因「无视频」退出；可手动放 1-5 分钟视频到 dataset/ 根下）")
    else:
        print(f"  [warn] 未找到 {src_videos}（可手动放视频到 dataset/ 根下）")

    weights_dir = DATASET / "dataset_weights"
    if src_models.is_dir():
        pths = sorted(src_models.glob("*.pth"))
        if pths:
            weights_dir.mkdir(parents=True, exist_ok=True)
            for f in pths:
                shutil.copy2(f, weights_dir / f.name)
            print(f"     权重已平铺：{[f.name for f in pths]} → dataset_weights/（Kaggle 侧 verify.py 重命名回 weights/）")
        else:
            print("  [warn] 未找到 *.pth 权重——Kaggle 上 TrackNet 会 CvUnavailable 级联跳过（验证链路仍通）。")
            print("     要真跑推理，先 backend/scripts/fetch_tracknet_weights.py 生成 tracknet.pth 放入 backend/data/models/。")

    # 6. 提示
    total = sum(f.stat().st_size for f in DATASET.rglob("*") if f.is_file())
    print(f"\n  [ok] dataset 已组装：{DATASET}（约 {total / 1048576:.1f} MB，Kaggle 上限 5GB）")
    return True


def verify_dataset_content() -> bool:
    """dataset/ 内容完整性：metadata + verify.py + app 包 + 视频/权重（平铺布局）。"""
    ok = True
    if not DATASET.joinpath("dataset-metadata.json").is_file():
        print("  [FAIL] 缺 dataset-metadata.json（kaggle CLI 2.x 认的新版 metadata；旧 meta.json 已不读）"); ok = False
    if not DATASET.joinpath("verify.py").is_file():
        print("  [FAIL] 缺 verify.py"); ok = False
    if not (DATASET.joinpath("app.tar.gz").is_file() or DATASET.joinpath("app.zip").is_file()):
        print("  [FAIL] 缺 app.tar.gz / app.zip（app 代码包）"); ok = False
    vids = [f for f in DATASET.glob("*.mp4")]
    pths = list(DATASET.joinpath("dataset_weights").glob("*.pth")) if DATASET.joinpath("dataset_weights").is_dir() else []
    if not vids:
        print("  [warn] dataset/ 根下无 mp4 视频——Kaggle 跑起来会因「无视频」退出。")
        print("     先放 1-5 分钟网球视频到 kaggle/dataset/ 根下（文件名避开 dataset_sample.mp4 可多放）再重跑")
    if not pths:
        print("  [warn] dataset_weights/ 下无 .pth 权重——Kaggle 上 TrackNet 会 CvUnavailable 级联跳过（验证链路仍通）。")
        print("     要真跑推理，先 backend/scripts/fetch_tracknet_weights.py 生成 tracknet.pth 放入 backend/data/models/。")
    if ok:
        print(f"  [ok] dataset 内容就绪（平铺布局：视频 {len(vids)} 个 / 权重 {len(pths)} 个，kaggle CLI 2.x dir_mode=skip 可全量上传）")
    return ok


def _kaggle_username() -> str | None:
    """Kaggle 用户名（新版 CLI token 模式不一定能从本地配置解出；解不出时返回 None，
    由调用方提示用户手填 dataset-metadata.json 的 id）。"""
    try:
        from kaggle.api.kaggle_api_extended import KaggleApi  # type: ignore
        api = KaggleApi()
        user = api.config_values.get("username") or api.config_values.get("user")
        if isinstance(user, str) and user:
            return user
    except Exception:
        pass
    # 旧版 kaggle.json 兜底
    kj = Path.home() / ".kaggle" / "kaggle.json"
    if kj.is_file():
        try:
            return json.loads(kj.read_text(encoding="utf-8")).get("username")
        except (OSError, json.JSONDecodeError):
            pass
    return None


def upload_dataset(slug: str) -> bool:
    """kaggle datasets create -p .（在 kaggle/ 根下跑；CLI 认 dataset/ 内的 dataset-metadata.json）。

    kaggle CLI 2.x 的 -p 指向「含 dataset-metadata.json 的文件夹」，且默认 dir_mode=skip
    只上传该文件夹根下的平铺文件（子目录不传）——build_dataset 已产出平铺布局。
    这里 cwd=ROOT 跑 `kaggle datasets create -p dataset`。
    """
    print(f"  上传 dataset -> {slug}")
    code, out, err = sh(["kaggle", "datasets", "create", "-p", "dataset"], cwd=ROOT, env=kaggle_env())
    if out and out.strip():
        print(out)
    if err and err.strip():
        print(err)
    if code != 0:
        combined = (out + "\n" + err).lower()
        print(f"  [FAIL] 上传失败（退出码 {code}）")
        if "invalid folder" in combined:
            print(f"     原因：{DATASET} 目录不存在——先跑：python {ROOT / 'deploy.py'} --skip-upload 组装，再重传。")
        if "metadata" in combined:
            print(f"     metadata 问题：检查 {DATASET / 'dataset-metadata.json'}（kaggle CLI 2.x 新版 schema：")
            print(f"     id 须为 <你的用户名>/{slug}、title 6-50 字符、licenses 恰 1 项；旧 meta.json 已不读）")
        if "authentication" in combined or "auth" in combined:
            print("     凭据问题（kaggle CLI 2.x 需要新版 token）：")
            print("       1) 运行 kaggle auth login（浏览器授权，token 缓存到 ~/.kaggle/access_token）")
            print(f"       2) 或编辑 {DOTENV_NAME} 写入 KAGGLE_API_TOKEN=你的token 后重跑本脚本")
        print(f"     手动重传：cd {ROOT} && kaggle datasets create -p dataset")
        return False
    print(f"  [ok] dataset 已上传 Kaggle：{slug}")
    return True


def push_notebook(kernel_slug: str) -> bool:
    """kaggle kernels push -p . 把 kaggle_verify.ipynb 推成 Kaggle notebook。"""
    ipynb = ROOT / "kaggle_verify.ipynb"
    if not ipynb.is_file():
        print(f"  [FAIL] 缺 notebook 模板 {ipynb}")
        return False
    print(f"  推送 notebook -> {kernel_slug}")
    code, out, err = sh(["kaggle", "kernels", "push", "-p", "."], cwd=ROOT, env=kaggle_env())
    if out and out.strip():
        print(out)
    if code != 0:
        combined = (out + "\n" + err).lower()
        print(f"  [FAIL] notebook 推送失败（退出码 {code}）：{(err or out).strip()[:400]}")
        if "authentication" in combined or "auth" in combined:
            print("     凭据问题：运行 kaggle auth login 或写 kaggle/.env 的 KAGGLE_API_TOKEN，再重跑。")
        return False
    print(f"  [ok] notebook 已推送")
    print(f"    在 https://www.kaggle.com/kernels 搜 '{kernel_slug}' 找到")
    return True


def print_manual_steps(slug: str, kernel_slug: str) -> None:
    """Kaggle 侧人工步骤 + 完成后拉结果命令。"""
    print("\n" + "=" * 60)
    print("下一步（人工，Kaggle 网页）：")
    print("=" * 60)
    print("  1) 打开 https://www.kaggle.com/kernels 搜 " + "'" + kernel_slug + "'" + "（或你的用户名下）")
    print("     Settings -> Input Data 勾 '" + slug + "'")
    print("     Settings -> Accelerator 选 GPU T4 16GB / P100")
    print("     Settings -> Internet 勾 ON（CLIP 需下载 HF 权重）")
    print("     点 Run All 运行（单条视频 1-5 分钟不会触 9h 上限）")
    print("")
    print("  2) 运行完成后（OUTPUT 区出现 track_overlay.mp4）拉结果：")
    print("     kaggle kernels pull <你的用户名>/" + kernel_slug + " -p out/")
    print("     或网页直接下载 /kaggle/working/ 下的产物")
    print("")
    print("  3) 人工核对（对齐方案 14 §2.4）：")
    print("     out/ 下各 kaggle-XX/track_overlay.mp4 + result.json + summary.json")
    print("     - G1：金标回合内球轨迹是否连续（红未检出=漏检）")
    print("     - G2：静止段（灰标）是否落在回合外；与回合重叠=死球误入")
    print("     - 多球跳变：调 post.visualize_track 的 max_speed/break_gap 重跑")


def _safe_stdout() -> None:
    """Windows 控制台 GBK 无法打印 UTF-8 特殊符号/中文——强制 stdout/stderr 切 UTF-8（PEP 540）。

    失败（如流已被重定向且不可重配）则静默降级，不阻断脚本。
    仅 main() 入口调用一次；直接 import 单函数时不会触发，
    故函数体内一律用 ASCII 安全符（[ok]/[FAIL]/[warn]）打印，不依赖此调用。
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
    next_step("组装 kaggle/dataset/（纯 Python：shutil/tarfile，平铺布局，无 shell 依赖）")
    if not build_dataset(slug=args.slug):
        print("\n终止：dataset 组装失败。检查上方报错（dataset-metadata.json/verify.py/app.tar.gz/视频/权重）。")
        return 1

    # 4) 内容完整性
    next_step("dataset 内容完整性")
    verify_dataset_content()

    if args.skip_upload:
        owner = _kaggle_username()
        if not owner:
            print("\n[完成] --skip-upload：只组装本地 dataset，未上传。")
            print(f"       手动上传前：1) 配凭据（kaggle auth login 或 {DOTENV_NAME} 写 KAGGLE_API_TOKEN）")
            print("       2) 编辑 dataset/dataset-metadata.json 的 id 为 <你的Kaggle用户名>/" + args.slug)
            print(f"       3) 手动上传：cd {ROOT} && kaggle datasets create -p dataset")
        else:
            print("\n[完成] --skip-upload：只组装本地 dataset，未上传。")
            print(f"       手动上传：cd {ROOT} && kaggle datasets create -p dataset")
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
