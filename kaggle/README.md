# Kaggle GPU 验证包（TennisClip · 方案 14 Step 2/6）

把本项目「CV 增强调试工作流」（`detect.tracknet` → `post.score_highlights` → `edit.concat` / `report.technical` / `post.visualize_track`）完整移植到 Kaggle 免费 GPU Notebook 上运行，
**代码零分叉**：`verify.py` 通过 `--repo` 指向打包进 dataset 的 `backend/app`，直接 `import app.workflow.*` 跑 16 节点 executor 图，
产物（`track_overlay.mp4` / 候选与评分 JSON / 集锦）落 `/kaggle/working/` 供下载，
用于**人工验证** TrackNet 是否检出球、静止球是否混入候选（对齐方案 14 §2.4 可视化人工验证）。

## 目录结构

```
kaggle/
├── README.md              # 本文件：使用说明
├── verify.py              # 验证入口（Kaggle Notebook 里 `python verify.py ...` 跑）
├── kaggle_verify.ipynb    # Notebook 模板（已配 P100/T4 + Internet + 输入 dataset）
├── build_dataset.ps1      # 一键组装：backend/{app,prompts} + 权重 + 视频 + verify.py → dataset/
├── deploy.py              # 一键部署（Python 标准库，调 kaggle CLI）：组装+datasets create+kernels push
├── .env                   # Kaggle API 凭据（KAGGLE_USER_NAME/KAGGLE_API_KEY，.gitignore 忽略，不入仓）
└── dataset/               # 本地组装区（.gitignore 忽略，不入库）
    ├── data/              #   待验证视频（1–5 分钟，720p/30fps 更佳）
    ├── weights/           #   TrackNet 权重（tracknet.pth，TorchScript 或 torch.save 完整模型）
    └── meta.json          #   kaggle datasets create 所需元数据（build_dataset.ps1 生成）
```

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
> **权重路径**：`verify.py` 通过 `cv_runtime.MODELS_DIR` 指向 dataset 的 `weights/` 目录，使 `resolve_weights` 缺省路径命中 `tracknet.pth`，节点侧无需改 spec。
>
> **日志受限环境**：`verify.py` 在 `--dry-run` 或 Windows 平台自动置 `TENNISCLIP_LOG_ENQUEUE=0`，关 loguru 的 `enqueue`（`multiprocessing.SimpleQueue` 在 Windows 沙箱权限受限），Kaggle Linux 上保持 `enqueue=1` 异步写。
>
> **本地 dry-run**：`python kaggle/verify.py --dry-run --repo backend --input kaggle/dataset --out kaggle/dataset/out`——仅验证 import 闭包 + 图编译 + `PureExecutor` 构建，不跑逐帧推理（无 GPU/无真实权重也能过）。

## 验证链路（与「CV 增强调试工作流」一一对应）

| 工作流节点 | verify.py 对应 | 说明 |
|---|---|---|
| `preprocess.transcode` | `preprocess.preprocess` | 统一 720p/30fps（对齐 `backend/app/utils/ffmpeg.py` 规格） |
| `detect.tracknet` | `cv_tracknet.track_video(device="cuda")` | GPU 逐帧推理，权重从 dataset `weights/tracknet.pth` |
| `event_detect.track_to_candidates` | `event_detect.track_to_candidates` | 需 `HighlightConfig`（verify.py 内置默认值，与 `config.yaml` 一致） |
| `post.score_highlights`（CLIP） | `cv_clip_score.score_windows` | 开 Internet 下载 HF 权重；T4 16GB 可跑 small/base |
| `edit.concat` / `report.technical` | `video_editor` / `report` | LLM 节点走 Mock 模式（`llm.mock_mode: auto`，无 API Key 自动降级） |
| `post.visualize_track` | `cv_visualize.render_track_overlay` | 产物 `/kaggle/working/track_overlay.mp4` |
| G2 静止球核对 | `cv_visualize.split_tracklets` + `is_static_segment` | 输出静止段清单（人工核对 G2 死球误入） |

## 快速开始（本地准备 → Kaggle 推送 → 下载结果）

```powershell
# 0. 前置：装 kaggle CLI + 配凭据（三选一，deploy.py 按「环境变量 > kaggle/.env > ~/.kaggle/kaggle.json」优先级识别）
pip install kaggle
#   方式 A（推荐，随仓库不入库）：kaggle/.env（.gitignore 已忽略）
#     Set-Content kaggle\.env @"`nKAGGLE_USER_NAME=你的用户名`nKAGGLE_API_KEY=你的APIKey"@ -Encoding UTF8
#     （kaggle.com → 账户 → Create New API Token）
#   方式 B：设环境变量 KAGGLE_USER_NAME / KAGGLE_API_KEY
#   方式 C：mkdir $HOME\.kaggle  把 kaggle.json 放里面

# 1. 本地准备：视频 + 权重（可选，缺了 Kaggle 侧 TrackNet 级联跳过但链路仍通）
cd kaggle
New-Item -ItemType Directory -Force dataset\data, dataset\weights
Copy-Item ..\backend\data\sample_videos\*.mp4 dataset\data\
Copy-Item ..\backend\data\models\tracknet.pth dataset\weights\
#    若权重缺失，本地先跑 backend/scripts/fetch_tracknet_weights.py 生成

# 2. 一键部署（组装 + 上传 dataset + 推 notebook，全自动）
python deploy.py
#    等价于手动：powershell -File .\build_dataset.ps1
#              → kaggle datasets create -p dataset
#              → kaggle kernels push -p .
#    只组装本地不上传：python deploy.py --skip-upload --allow-missing-cli

# 3. Kaggle 侧人工（deploy.py 末尾会打印）：
#    https://www.kaggle.com/kernels 搜 kaggle-tennisclip-verify
#    Settings → Input Data 勾 {username}/kaggle-tennisclip-verify
#    Settings → Accelerator 选 T4/P100
#    Settings → Internet 勾 ON（CLIP 需下载 HF 权重；GPU 配额按开联网计）
#    点 "Run All" 运行

# 4. 运行完成后拉结果
kaggle kernels pull {username}/kaggle-tennisclip-verify -p out\
#    拿到 out\kaggle-00\track_overlay.mp4 + result.json + summary.json
```
# 5. 下载结果
kaggle kernels pull {username}/kaggle-tennisclip-verify -p out\
#    拿到 out\kaggle-00\track_overlay.mp4 + result.json + summary.json
```

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

- `verify.py` / `build_dataset.ps1` / `kaggle_verify.ipynb` **入库**（可复现验证流程）；
- `dataset/`（视频、权重、组装产物）`.gitignore` 忽略（对齐 `backend/data/` 惯例，避免生成数据库/权重/视频文件入库——AGENTS.md 边界约定）。
