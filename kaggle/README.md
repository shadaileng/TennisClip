# Kaggle GPU 验证包（TennisClip · 方案 14 Step 2/6）

把本项目「CV 增强调试工作流」（`detect.tracknet` → `post.score_highlights` → `edit.concat` / `report.technical` / `post.visualize_track`）完整移植到 Kaggle 免费 GPU Notebook 上运行，
**代码零分叉**：`verify.py` 通过 `--repo` 指向打包进 dataset 的 `backend/app`，直接 `import app.workflow.*` 跑 16 节点 executor 图，
产物（`track_overlay.mp4` / 候选与评分 JSON / 集锦）落 `/kaggle/working/` 供下载，
用于**人工验证** TrackNet 是否检出球、静止球是否混入候选（对齐方案 14 §2.4 可视化人工验证）。

## 目录结构

```
kaggle/
├── README.md              # 本文件：详细操作步骤
├── verify.py              # 验证入口（Kaggle Notebook 里 `python verify.py ...` 跑）
├── kaggle_verify.ipynb    # Notebook 模板（GPU + Internet + 自动挂载输入 dataset）
├── kernel-metadata.json   # notebook 推送元数据（kaggle CLI 2.x 必需；deploy.py 自动幂等生成）
├── build_dataset.ps1      # 独立 PowerShell 一键组装（供习惯 PowerShell 的用户单独用；deploy.py 不调它）
├── deploy.py              # 一键部署（Python 标准库，调 kaggle CLI）：组装+datasets create+version+kernels push
├── .env                   # Kaggle API 凭据（.gitignore 忽略，不入仓；deploy.py 标准库解析并注入 CLI 子进程）
└── dataset/               # 本地组装区（.gitignore 忽略，不入库；kaggle CLI 2.x 上传后形态 = 全平铺）
    ├── dataset-metadata.json  # kaggle CLI 2.x 新版 metadata（全字段 ASCII；deploy.py 生成）
    ├── verify.py                 # 验证入口副本（开跑前规整平铺布局 → data/ + weights/）
    ├── app.tar.gz                # backend/{app,prompts} 打包（notebook 解压到 /kaggle/working/）
    ├── dataset_sample.mp4        # 视频（根下平铺；多视频放根下，文件名避开 dataset_sample.mp4）
    └── tracknet.pth              # 权重（根下平铺，可多个 *.pth）
```

> **上传后形态 = 全平铺**：kaggle CLI 2.x 默认 `dir_mode=skip` 只上传 `dataset/` 根下的平铺文件、**子目录不传**
> （CLI 会打印 `Skipping folder: xxx`）。故 deploy.py 把视频、权重、代码包全部平铺在根下，
> Kaggle 侧 `/kaggle/input/{slug}/` 是纯平铺，`verify.py` 开跑前由 `normalize_input_layout` 幂等规整回 `data/` + `weights/`。

## 移植边界（关键设计）

| 层 | 是否移植 | 说明 |
|---|:---:|---|
| `app/workflow/*`（16 节点 + spec + graph + executor + presets） | ✅ 完整 | 节点 `run(ctx, params)` 是纯函数（AGENTS.md 独立性契约：执行器注入 `copy.deepcopy` 的独立 `ctx.config`，节点不直调 `db_service`），Kaggle 上直接可跑 |
| `app/services/*`（preprocess / event_detect / video_editor / report / segment_ops / highlight） | ✅ 完整 | 节点依赖的业务层，全部纯函数 |
| `app/utils/cv_*`（7 个薄封装）+ `cv_runtime` + `ffmpeg` + `logger` + `cancel` | ✅ 完整 | 依赖 `torch/opencv/ultralytics/transformers/loguru`，Kaggle pip 可装 |
| `app/models.py`（pydantic 结构化结果） | ✅ 完整 | 纯数据模型 |
| `app/config.py`（AppConfig 内存构造） | ✅ 完整 | Kaggle 无 `config.yaml`，用 `AppConfig()` 默认值（= `config.yaml` 缺省） |
| `prompts/`（领域 Prompt 模板） | ✅ 完整 | `report.technical` 经 `build_report_prompt` 注入网球教学知识库 |
| `app/db.py` / `db_models.py` / `db_service.py` / `main.py` / `routers/` | ❌ 不移植 | FastAPI/数据库层，Kaggle 上无需求；`verify.py` 用 `PureExecutor` 复刻 executor 的「分层并行 + Context 传值 + 级联跳过 + 产物投影」核心（约 150 行），落库部分替换为写 JSON 到 `/kaggle/working/` |

> `verify.py` 的 `PureExecutor` 与 `app.workflow.executor.Executor` 同构，唯一区别是**收尾不调 `db_service.record_task_*`**（无数据库），产物写 `{out}/{task_id}/result.json`。验证的是「节点算法 + 图调度 + 降级契约」的正确性，而非 DB 持久化。
>
> **权重路径**：`verify.py` 通过 `cv_runtime.MODELS_DIR` 指向规整后的 `weights/` 目录，使 `resolve_weights` 缺省路径命中 `tracknet.pth`，节点侧无需改 spec。
>
> **布局规整**：`verify.py` 开跑前调 `normalize_input_layout` 把平铺布局幂等规整为 `data/` + `weights/`（根下 `*.mp4` → `data/`、根下 `*.pth/*.pt` → `weights/`）；本地 dry-run 的 `data/` + `weights/` 规整布局不受影响。
>
> **日志受限环境**：`verify.py` 在 `--dry-run` 或 Windows 平台自动置 `TENNISCLIP_LOG_ENQUEUE=0`，关 loguru 的 `enqueue`（`multiprocessing.SimpleQueue` 在 Windows 沙箱权限受限），Kaggle Linux 上保持 `enqueue=1` 异步写。
>
> **本地 dry-run**：`python kaggle/verify.py --dry-run --repo backend --input kaggle/dataset --out kaggle/dataset/out`——仅验证 import 闭包 + 图编译 + `PureExecutor` 构建，不跑逐帧推理（无 GPU/无真实权重也能过）。

## 验证链路（与「CV 增强调试工作流」一一对应）

| 工作流节点 | verify.py 对应 | 说明 |
|---|---|---|
| `preprocess.transcode` | `preprocess.preprocess` | 统一 720p/30fps（对齐 `backend/app/utils/ffmpeg.py` 规格） |
| `detect.tracknet` | `cv_tracknet.track_video(device="cuda")` | GPU 逐帧推理，权重从 dataset 规整后的 `weights/tracknet.pth` |
| `event_detect.track_to_candidates` | `event_detect.track_to_candidates` | 需 `HighlightConfig`（verify.py 内置默认值，与 `config.yaml` 一致） |
| `post.score_highlights`（CLIP） | `cv_clip_score.score_windows` | 开 Internet 下载 HF 权重；T4 16GB 可跑 small/base |
| `edit.concat` / `report.technical` | `video_editor` / `report` | LLM 节点走 Mock 模式（`llm.mock_mode: auto`，无 API Key 自动降级） |
| `post.visualize_track` | `cv_visualize.render_track_overlay` | 产物 `/kaggle/working/track_overlay.mp4` |
| G2 静止球核对 | `cv_visualize.split_tracklets` + `is_static_segment` | 输出静止段清单（人工核对 G2 死球误入） |

## 详细操作步骤

> 环境约定：**以 bash 环境为主**（Linux / macOS / Git Bash / MINGW64），Windows 原生 PowerShell 亦可。
> `deploy.py` 的 dataset 组装是**纯 Python 标准库**（shutil/tarfile/json），不依赖 bash、PowerShell 或 tar CLI，
> 因此 bash 与 Windows 终端里 `python deploy.py` 行为完全一致。`build_dataset.ps1` 作为独立脚本保留供单独使用。

### 步骤 0 · 装 kaggle CLI + 配凭据（一次性）

```bash
pip install kaggle            # 已装可跳过
```

**凭据三选一**（kaggle CLI 2.x 推荐新版 token，deploy.py 自动识别）：

| 方式 | 命令 | 说明 |
|---|---|---|
| A（推荐） | `kaggle auth login` | 浏览器 OAuth 授权，token 缓存到 `~/.kaggle/access_token`，零管理 |
| B | 编辑 `kaggle/.env` 写 `KAGGLE_API_TOKEN=你的token` | 随仓库不入库（.gitignore 忽略）；token 在 kaggle.com → Settings → API → Generate New Token |
| C | 设环境变量 `KAGGLE_API_TOKEN` | 临时，不进文件 |

> **旧版 `KAGGLE_USER_NAME` + `KAGGLE_API_KEY` + `~/.kaggle/kaggle.json` 已逐步弃用**：kaggle CLI 2.x 默认不再读取 kaggle.json。
> 旧凭据上传 dataset 可能走宽松路径成功，但 `datasets version`（`CreateDatasetVersion` RPC）等**写接口会 403 Forbidden**（凭据降级）。
> 遇到 403 就换方式 A 或 B 的新版 token。

### 步骤 1 · 本地准备视频/权重（可选）

```bash
# 不预填则 deploy.py 自动从 backend/data/ 拷（sample_videos/*.mp4 + models/*.pth）
# 权重缺失时本地先跑：
cd backend
uv run python scripts/fetch_tracknet_weights.py    # HF 源下载 → 契约适配 → TorchScript 导出 tracknet.pth
```

手动预填时放 `kaggle/dataset/` **根下**（全平铺，kaggle CLI 2.x 子目录不传）：

```
kaggle/dataset/dataset_sample.mp4   # 1–5 分钟网球视频（720p/30fps 更佳；多视频文件名避开 dataset_sample.mp4）
kaggle/dataset/tracknet.pth         # 权重（可多个 *.pth）
```

### 步骤 2 · 一键部署（组装 + 上传 dataset + 推 notebook）

```bash
cd kaggle
python deploy.py
```

`deploy.py` 按顺序做（纯 Python 组装，无 shell 依赖）：

1. **kaggle CLI 预检**（缺 CLI 时 `--allow-missing-cli` 可跳过）
2. **凭据预检**（识别新版 token / `.env` / 旧版 user:pass，打印来源与降级提示）
3. **组装 `kaggle/dataset/`**（`shutil.copy2` + `tarfile` 打 `app.tar.gz` + 平铺视频/权重 + 生成 `dataset-metadata.json`）
4. **内容完整性校验**（metadata / verify.py / app.tar.gz / 根下视频权重）
5. **上传 dataset**（`kaggle datasets create -p dataset`；slug 已存在时自动降级 `kaggle datasets version` 补新版本）
6. **推 notebook**（自动幂等生成 `kernel-metadata.json` + `kaggle kernels push -p .`）
7. **打印 Kaggle 侧人工步骤 + 结果拉取命令**

常用变体：

```bash
python deploy.py --skip-upload --allow-missing-cli   # 只组装本地 dataset，不上传/不推（无需 CLI 与凭据）
python deploy.py --slug my-dataset-slug              # 自定义 dataset slug
python deploy.py --kernel-slug my-kernel-slug        # 自定义 notebook slug
```

**手动分步**（等价于 deploy.py 各步骤，排障时用）：

```bash
cd kaggle
python deploy.py --skip-upload --allow-missing-cli    # 仅组装本地 dataset/
kaggle datasets create -p dataset                      # 首次上传 dataset
kaggle datasets version -m "add weights" -p dataset    # dataset 已存在时补新版本（不重建）
kaggle kernels push -p .                               # 推 notebook（需 kernel-metadata.json，deploy.py 已生成）
```

### 步骤 3 · Kaggle 网页人工设置 + 运行

`deploy.py` 末尾会打印这段，或手动：

1. 打开 notebook：`https://www.kaggle.com/code/<你的用户名>/<notebook-slug>`
   （kaggle CLI 2.x 用 `title` 自动生成 URL slug，与 metadata 的 `id` 后缀可能不一致——非阻断告警，实际 URL 以推送输出为准）
2. **Settings**：
   - **Input Data**：自动挂载 dataset（`kernel-metadata.json` 的 `dataset_sources` 已写 `<用户名>/<dataset_slug>`，Kaggle 取**最新版**）；未自动挂载则手动勾
   - **Accelerator**：选 **GPU T4 (16GB)** 或 **P100**
   - **Internet**：勾 **ON**（CLIP 需下载 HF 权重；GPU 配额按开联网时长计）
3. 点 **"Run All"** 运行（单条 1–5 分钟视频不会触 9h 上限；逐帧 GPU 推理 10 分钟起）

### 步骤 4 · 拉取结果 + 人工核对

```bash
# 运行完成后（notebook OUTPUT 区出现 track_overlay.mp4）拉结果：
kaggle kernels pull <你的用户名>/<notebook-slug> -p out/
# 产物：out/kaggle-*/track_overlay.mp4 + result.json + summary.json
```

人工核对（对齐方案 14 §2.4）：

- **G1**：金标回合内球轨迹是否连续（红色未检出 = 漏检）
- **G2**：静止段（灰标）是否落在回合外；与回合重叠 = 死球误入候选
- **多球跳变**：调 `post.visualize_track` 的 `max_speed` / `break_gap` 重跑

## kaggle CLI 2.x 上传约定（deploy.py 已按此产出，手动传时也须遵守）

> 本次会话踩过的坑，全部写进 deploy.py 自动处理；手动操作时务必遵守：

| 约定 | 说明 | 违反后果 |
|---|---|---|
| dataset 只认 `dataset-metadata.json` | 新版 schema：`id`=`<用户名>/<slug>`、`title` 6–50 字符、`licenses` 恰 1 项；旧版 `meta.json` 的 `dataset_name`/`license` 已不读 | 报 `Metadata file not found` |
| **metadata 全字段必须 ASCII** | kaggle CLI 用**系统默认编码**读 JSON（中文 Windows = GBK），中文字段会 `UnicodeDecodeError` 崩溃 | 上传即崩 |
| `id` 前缀 = 真实 Kaggle 用户名 | 占位 `YOUR_USERNAME` 时上传可能 403（owner 与 token 不匹配）；deploy.py 从 `kaggle/.env` 的 `KAGGLE_USER_NAME` 自动解出 | 403 Forbidden |
| **`dir_mode=skip`（默认）只传平铺文件** | 子目录不传（CLI 打印 `Skipping folder: xxx`）。故 dataset 全部产物平铺根下：`dataset_sample.mp4` + `tracknet.pth`；要连子目录全传需 `-r zip`/`-r tar`（会多套一层压缩包，勿用） | 权重/视频被静默跳过 |
| 缺 `dataset/` 目录时 `-p` 报错 | 先 `python deploy.py --skip-upload` 组装再传 | 报 `Invalid folder` |
| dataset 已存在时用 `datasets version` 补新版本 | 不用 `datasets create` 重建（slug 已占）；`kaggle datasets version -m <说明> -p dataset` | create 撞已存在 |
| **`datasets version` / 写接口 403** | 旧版 `KAGGLE_USER_NAME`/`KAGGLE_API_KEY` 在 CLI 2.x 写接口被降级鉴权；换 `kaggle auth login`（OAuth）或 `KAGGLE_API_TOKEN`（新版 token） | 403 Forbidden（`CreateDatasetVersion`） |
| **notebook 推送需 `kernel-metadata.json`** | kaggle CLI 2.x 官方 schema（`docs/kernels_metadata.md`）：必需 `id`/`title`/`code_file`（.ipynb 相对路径，**不是** `source_file`）/`language`/`kernel_type`；可选 `is_private`/`enable_gpu`/`enable_internet`/`machine_shape`/`dataset_sources`（`<用户名>/<dataset-slug>`，notebook 打开时自动挂载输入）。全 ASCII，同 dataset-metadata。deploy.py 的 `push_notebook()` 自动幂等生成 | 缺文件报 `Metadata file not found`；缺 `code_file` 报 `A source file must be specified` |
| **`title` 须与 `id` 后缀对齐** | kaggle CLI 2.x 用 `title` 转 slug 作为实际 URL（`TennisClip CV verify` → `tennisclip-cv-verify`），与 metadata `id` 后缀不一致时告警（非阻断）。deploy.py 现在从 `kernel_slug` 自动生成 `title`，重推不再告警 | URL 与 metadata id 不一致告警 |

## 已知限制

| 限制 | 影响 | 应对 |
|---|---|---|
| Kaggle 单 Notebook 9h 上限 | 单条 1–5 分钟视频不会触顶；批量跑累计 | 分批提交多个 notebook，或 `--frame-stride 2` 降采样 |
| P100/T4 每月 30h 免费 | 够跑几十条验证视频 | 批量验证时开 `--frame-stride 2` 降 GPU 负载 |
| T4 16GB | CLIP large 可能 OOM | `post.score_highlights` 默认 `clip_model=small`（base 备选） |
| 权重需随 dataset 上传 | tracknet.pth 约几十 MB，5GB 上限无压力 | `fetch_tracknet_weights.py` 一键生成 |
| HF CLIP 权重需联网 | Notebook 必须开 Internet 开关 | Settings → Internet ON |
| LLM 节点 Mock 降级 | `report.technical` / `analyze.highlight` 无 API Key 时输出占位结果 | 验证「链路正确性」正合适；要验证真实 LLM 需开 Internet 填 `ai.provider=custom` + base_url + api_key（免费配额下不划算） |

## 与仓库的关系

- `verify.py` / `kaggle_verify.ipynb` / `kernel-metadata.json`（幂等生成物，可入库）/ `build_dataset.ps1` / `deploy.py` **入库**（可复现验证流程）；
- `dataset/`（视频、权重、组装产物 `app.tar.gz`）与 `.env`（Kaggle API 凭据）`.gitignore` 忽略（对齐 `backend/data/` 惯例，避免生成数据库/权重/视频文件/凭据入库——AGENTS.md 边界约定）。
