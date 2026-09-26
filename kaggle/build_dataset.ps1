# build_dataset.ps1 — 组装 Kaggle dataset 包（backend/app + 权重 + 视频 + verify.py）
#
# 用法：
#   cd kaggle
#   powershell -File .\build_dataset.ps1
#   kaggle datasets create -p dataset
#
# 产物：kaggle_dataset.zip（由 kaggle CLI 从 dataset/ 目录创建，本脚本只组装 dataset/ 内容）
#
# dataset/ 目录布局（kaggle CLI 会按此布局上传）：
#   dataset/
#   ├── meta.json              # kaggle datasets create 所需元数据
#   ├── verify.py              # 验证入口（本目录）
#   ├── app.tar.gz             # backend/app 打包（Kaggle 上解压后 PYTHONPATH 指向）
#   ├── data/*.mp4             # 待验证视频（从 backend/data/sample_videos/ 拷贝）
#   └── weights/*.pth          # TrackNet 权重（从 backend/data/models/ 拷贝）

$ErrorActionPreference = "Stop"

$KAGGLE_DIR = $PSScriptRoot
$REPO_ROOT  = (Resolve-Path "$KAGGLE_DIR\..").Path
$BACKEND    = "$REPO_ROOT\backend"
$DATASET    = "$KAGGLE_DIR\dataset"

Write-Host "==> 组装 Kaggle dataset 包" -ForegroundColor Cyan
Write-Host "    仓库根：$REPO_ROOT"
Write-Host "    dataset：$DATASET"

# 0. 清理旧 dataset（保留 data/weights 由用户预填的视频/权重；只重建 meta/verify/app.tar.gz）
if (Test-Path "$DATASET\app.tar.gz") { Remove-Item "$DATASET\app.tar.gz" }
if (Test-Path "$DATASET\app.zip") { Remove-Item "$DATASET\app.zip" }

# 1. 校验前置
if (-not (Test-Path "$BACKEND\app")) {
    throw "未找到 $BACKEND\app，请确认在仓库内运行"
}
if (-not (Test-Path "$KAGGLE_DIR\verify.py")) {
    throw "未找到 $KAGGLE_DIR\verify.py"
}
# 依赖检查（Kaggle notebook 会 pip 装；本地 dry-run 需 backend/.venv 已 uv sync）
# 注意：用系统 python 探测（非 backend/.venv）；未装仅告警，不阻断（Kaggle 侧自装）
$DEPS = @("loguru", "pydantic", "yaml", "dotenv", "torch", "cv2", "ultralytics", "transformers")
$missing = @()
foreach ($d in $DEPS) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & python -c "import $d" 2>$null | Out-Null
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($code -ne 0) { $missing += $d }
}
if ($missing.Count -gt 0) {
    Write-Warning "以下依赖系统 python 未装（Kaggle 侧由 notebook cell 1 pip 安装；本地 dry-run 用 backend/.venv 即可）：$($missing -join ', ')"
}

# 2. meta.json（kaggle datasets create 需要）
# 布局说明（Kaggle 侧 dataset 根）：
#   verify.py / app.tar.gz / data/*.mp4 / weights/*.pth / meta.json
# verify.py 的 --repo 指向「解压根」（含 app/ + prompts/），--input 指向 dataset 根
# 注意：kaggle dataset 上传时若根目录有多文件 + meta.json，CLI 会按目录结构上传
New-Item -ItemType Directory -Force $DATASET | Out-Null
$metaJson = @{
    dataset_name = "kaggle-tennisclip-verify"
    title        = "TennisClip CV 验证包（代码+权重+视频）"
    version      = 1
    license      = "MIT"
} | ConvertTo-Json
$metaJson | Set-Content -Path "$DATASET\meta.json" -Encoding UTF8
Write-Host "    [1/4] meta.json 已生成"

# 3. 拷贝 verify.py
Copy-Item "$KAGGLE_DIR\verify.py" "$DATASET\verify.py" -Force
Write-Host "    [2/4] verify.py 已拷贝"

# 4. 打包 backend/{app,prompts} → dataset/app.tar.gz（Kaggle 上解压到 /kaggle/working/app/ + prompts/）
# report 节点 import prompts.highlight_analysis（顶层包），verify.py 的 --repo 指向解压根（含 app/ 与 prompts/）
$TAR_OUT = "$DATASET\app.tar.gz"
# 用 tar（Windows 10+ 自带）打包；若失败回退到 Python zipfile（产物改 app.zip，notebook 侧识别两种容器）
$tar_ok = $false
if (Get-Command tar -ErrorAction SilentlyContinue) {
    Push-Location $BACKEND
    tar -czf $TAR_OUT -C $BACKEND app prompts
    Pop-Location
    if ($LASTEXITCODE -eq 0 -and (Test-Path $TAR_OUT)) { $tar_ok = $true }
}
if ($tar_ok) {
    Write-Host "    [3/4] app.tar.gz 已生成（tar，含 app/ + prompts/）：$TAR_OUT"
} else {
    Write-Warning "tar 不可用或失败，改用 Python 打包为 app.zip"
    Remove-Item $TAR_OUT -ErrorAction SilentlyContinue
    $ZIP_OUT = "$DATASET\app.zip"
    # 写临时 Python 脚本再执行（避免 ps1 内联多行 python -c 的转义陷阱）
    $PY_SCRIPT = "$DATASET\_pack_app.py"
    @'
import zipfile, pathlib, sys
backend = pathlib.Path(sys.argv[1])
dst = pathlib.Path(sys.argv[2])
with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zf:
    for sub in ('app', 'prompts'):
        src = backend / sub
        for p in src.rglob('*'):
            if p.is_file():
                zf.write(p, p.relative_to(backend))
print('    [3/4] app.zip 已生成（Python，含 app/ + prompts/）:', dst)
'@ | Set-Content -Path $PY_SCRIPT -Encoding UTF8
    python $PY_SCRIPT "$BACKEND" "$ZIP_OUT"
    Remove-Item $PY_SCRIPT -ErrorAction SilentlyContinue
}

# 5. 拷贝视频与权重（若用户已预填 dataset/data、dataset/weights；否则从 backend 拷贝）
$DATA_DIR   = "$DATASET\data"
$WEIGHTS_DIR = "$DATASET\weights"
$src_videos = "$REPO_ROOT\backend\data\sample_videos"
$src_models = "$REPO_ROOT\backend\data\models"

if (Test-Path $src_videos) {
    New-Item -ItemType Directory -Force $DATA_DIR | Out-Null
    $copied = Copy-Item "$src_videos\*.mp4" $DATA_DIR -Force
    Write-Host "    [4/4] 视频已拷贝：$(@($copied).Count) 个 mp4 → $DATA_DIR"
} else {
    Write-Warning "未找到 $src_videos（用户可手动往 dataset/data/ 放视频）"
}

if (Test-Path $src_models) {
    New-Item -ItemType Directory -Force $WEIGHTS_DIR | Out-Null
    $pths = Get-ChildItem "$src_models\*.pth" -ErrorAction SilentlyContinue
    if ($pths) {
        $pths | ForEach-Object { Copy-Item $_.FullName $WEIGHTS_DIR -Force }
        Write-Host "    权重已拷贝：$($pths.Name) → $WEIGHTS_DIR"
    } else {
        Write-Warning "未找到 $src_models\*.pth（先跑 backend/scripts/fetch_tracknet_weights.py 生成 tracknet.pth）"
    }
}

# 6. 提示
$size_mb = [math]::Round((Get-ChildItem $DATASET -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host ""
Write-Host "==> dataset 组装完成：$DATASET（约 ${size_mb} MB，Kaggle 上限 5GB）" -ForegroundColor Green
Write-Host ""
Write-Host "下一步：" -ForegroundColor Yellow
Write-Host "  kaggle datasets create -p dataset"
Write-Host "  kaggle kernels push -p ."
Write-Host ""
Write-Host "Kaggle 侧（notebook 第二个 cell 自动识别 app.tar.gz / app.zip 两种容器）：" -ForegroundColor Yellow
Write-Host "  解压：tar -xzf /kaggle/input/{slug}/app.tar.gz -C /kaggle/working   （或 unzip app.zip）"
Write-Host "  运行：python /kaggle/input/{slug}/verify.py --repo /kaggle/working --input /kaggle/input/{slug} --out /kaggle/working --device cuda"
