# Changelog

本文件记录 TennisClip AI 所有重要变更。

格式基于 Keep a Changelog，版本号遵循语义化版本。

## [0.22.0] - 2026-09-25

### Added

- CV 感知层 GPU 接入（v0.22.0）：`cv_runtime.resolve_device` 统一设备解析（`auto`=有 CUDA 用 CUDA 否则 CPU，`cuda`/`cpu` 显式），`load_torch_model(path, device="auto")` 加载时 `.to(device).eval()`；`cv_tracknet` / `cv_court` / `cv_stroke_cls` 推理张量 `.to(dev)`，`cv_clip_score` / `cv_player`（YOLO）节点补 `device` ParamSpec；4 个节点（`detect.tracknet` / `detect.court` / `post.score_highlights` / `post.classify_strokes`）暴露 `device=auto` 参数，默认有 GPU 自动走 CUDA；`cv_tracknet` 每 100 采样帧打进度日志（便于长视频监控）；全量 317 测试通过、日志规范通过。实测 Tesla T4 环境 `resolve_device('auto')` → `cuda` ✓（当前容器未挂载 GPU 设备，`gpu` 字段返回 `null` 为正确降级）
- 前端实时资源监控面板（v0.22.0）：后端新增 `app/utils/system_stats.py`（CPU/内存/任务队列/GPU 聚合，数据源 cgroup v2 优先、回退 `/proc/stat`、`/proc/meminfo`，GPU 走 `nvidia-smi`），`GET /api/v1/system/stats` 端点；`frontend/src/components/ResourcePanel.vue` 常驻轮询（运行中任务 2s / 空闲 10s），带 CPU/内存进度条（<50% 绿 / <80% 黄 / ≥80% 红）、任务队列计数、GPU 卡信息（每卡两条独立进度条——利用率与显存分列带标签，避免两个不同维度百分比被误读为同一条，无设备显示「GPU 未检测到」）；App.vue 在 TaskCard 上方挂载 ResourcePanel；`tests/test_system_stats.py` 2 用例覆盖端点响应结构与 JSON 可序列化；全量 319 测试通过、日志规范通过
- 日志时间加 UTC 标志：`app/utils/logger.py` 的 `FMT_TEXT`/`FMT_CONSOLE` 时间格式改为 `{time:YYYY-MM-DD HH:mm:ss.SSSZ!UTC}`（loguru `!UTC` 后缀强制换算为 UTC、`Z` 令牌输出 `+00:00` 偏移），输出形如 `2026-09-25 02:52:47.004+00:00 | INFO ...`，与服务器时区解耦、跨机器日志时间一致，读取方按需换算本地时间便于本地化聚合；控制台彩色版同步保留 `<green>` 包裹；新增 `tests/test_logging.py` TC-07（正则断言 `\+00:00` 存在 + 解析时间与 `datetime.now(timezone.utc)` 相差 < 5s，证明确经换算而非贴标），`test_logging.py` 7 用例全绿、全量 318 测试通过；同步 `AGENTS.md` 统一格式规范与方案文档 `02-后端loguru日志TDD方案.md`（v1.0.3）
- 失管后台服务进程查询/清理脚本 `scripts/manage_services.py`（纯标准库、仅依赖 Linux `/proc`）：`list` 只读排查（PID/PPID/运行时长/端口/服务/是否孤儿，`--json` 机器可读，退出码 0=干净/1=有目标）、`clean --dry-run` 预览不发信号、`clean --yes` 按「SIGTERM → 等待校验 → 必要时 SIGKILL → 复验进程与端口」回收（退出码 0=已清干净/1=有残留）；识别 uvicorn/vite/esbuild/vitepress/`pnpm|npm|yarn dev`/`sh -c` 包装层并**向后代展开**（uvicorn multiprocessing 子进程、sh 中间层一并捕获）；安全过滤——永不清理自身进程链、IDE/code-server、grep/ps/rg 等检索工具，默认要求进程 cwd 在项目根内（`--any-cwd` 放开），非交互环境必须显式 `--yes`；新增 `backend/tests/test_manage_services.py` 21 用例（规则匹配矩阵/自身链保护/孤儿判定/端口识别/JSON 契约/dry-run 零信号）
- `AGENTS.md` 新增边界规则「禁止自动启动后台服务」：agent 不得以 `&`/`nohup`/`setsid`/`start`/`background` 启动常驻服务并放任后台运行（会话结束即成孤儿进程，端口占用、用户难回收）；确需启动须 ①前台或带超时 ②先征得用户同意 ③用完同轮内停止并校验退出；配套在「构建 / 运行 / 测试」补 `manage_services.py` 三条命令与「失管进程查询与回收」条目
- 新增文档 `docs/plans/13-场景图关系感知节点方案.md`（📋 待执行）及 `docs/README.md` 文档一览/执行进度、`docs/.vitepress/config.mts` 侧边栏同步

### Fixed

- CV 归一化表达式 `.to()` 误绑浮点字面量导致 tracknet 必炸（v0.22.4）：Python 属性访问优先级高于除法，`.float() / 255.0` 换行后接 `.to(dev)` 被解析为 `255.0.to(dev)`，float 无 `.to` 方法运行时抛 `'float' object has no attribute 'to'`——`detect.tracknet` 必失败 → `on_failure="skip"` 级联 n4~n7 全 skipped → 任务标记 succeeded 却无高光/集锦/报告；`cv_tracknet` / `cv_court` / `cv_stroke_cls` 三处改为 `.float().to(dev) / 255.0`（`.to` 提到除法之前，标量广播无害），新增 `tests/test_cv_ast_guard.py` 8 用例 AST 静态扫描 `cv_*.py` 禁止 float 字面量属性调用回潮；实测 Tesla T4 `推理设备=cuda`、200 采样帧 20s（~100ms/帧 vs CPU 2.5s/帧，约 46× 提速）、58 轨迹点置信度 0.9999，全量 339 测试通过
- CV 设备值类型防御守卫（v0.22.1）：`cv_runtime.ensure_device` 对 `float`/`None`/对象等非法类型打 warning 并回退 `auto`，所有 cv_* 模块改用它；`tests/test_cv_runtime_device.py` 13 用例。注：该守卫防的是 `dev` 参数类型错，防不住上述「接收者为 float 字面量」的解析错误（最初误判该错误为 `--reload` 字节码残留，实为运算符优先级 bug，二者互补）
- 资源面板统计口径修复（v0.22.6）：原 CPU% 用 `/proc/stat` 累计 tick 比值（= 开机以来平均，负载变化几乎不反映），改为**模块级采样基线 + 前后差分**（窗口 < 50ms 返回 None，首次请求只建基线显示 `—`）；数据源优先 **cgroup v2**——CPU 用量取自身 `cpu.stat` 的 `usage_usec`、配额沿祖先链向上找最近数值 `cpu.max`（容器常见子级 `max` 限制挂父级）、内存取 `memory.current` + 向上回溯 `memory.max`、核数取配额换算，回退 `/proc/stat`、`/proc/meminfo`、`os.cpu_count()`，避免容器内读到宿主全局数据（原 meminfo 总量曾显示宿主 258GB）；删除死代码 `_get_pid_cpu_percent` 与 `import resource`（算完未进返回值、`prev_cpu` 参数从未被传）；实测：6 进程满载 73% → 空闲 13.6%（原恒为 5% 左右），内存总量显示容器限制 32GB、核数 8 与 `cpu.max` 一致；`tests/test_system_stats.py` 扩至 10 用例（差分/短窗口/数据源切换/计数器回退/首采样基线/cgroup 解析），全量 347 测试通过
- 修复 `ResourcePanel` 调用 `api.url is not a function`（v0.22.3）：`api` 对象未导出内部 `url()` 函数，改为组件内联读取 `import.meta.env.VITE_API_BASE_URL` 拼接请求路径
- 前端错误信息换行显示（v0.22.2）：`TaskDetailModal` / `TaskHistory` 的 error 字段加 `break-words whitespace-pre-wrap`，多行错误不再挤占同行布局

### Changed

- 前端轮询自适应：当 `store.current.status` 变化时，`ResourcePanel` 强制立即刷新一次（与任务进度轮询频率联动）

## [0.21.0] - 2026-09-25

### Added

- 终态任务重试功能：`POST /api/v1/tasks/{task_id}/retry`（失败/被停止/超时任务按原 level + 输入视频 MD5 秒传重新提交，返回 `{retried_from, task_id, status}`；非终态 409、输入不可用 400、任务不存在 404；管线配置按提交时刻解析）；`get_task_detail` 返回值新增 `level` 字段（重试按原层级提交的依据）；前端 TaskCard 失败/超时卡片「↻ 重试」按钮 + TaskHistory 失败行「重试」按钮 + `api.retryTask` + store `retryTask(taskId?)`（成功后接入新任务轮询并刷新历史列表）；新增 `tests/test_retry_task.py` 6 用例，全量 295 测试通过

## [0.20.0] - 2026-09-25

### Added

- 任务停止功能（协作式取消）：新增 `app/utils/cancel.py`（模块级取消旗标 + `TaskCancelled` 异常 + `check_cancelled` 检查点）；`POST /api/v1/tasks/{task_id}/stop` 标记取消旗标（任务需在本进程内存队列，终态/重启后任务返回 stopped=false）；检查点接入——executor 每层节点边界、`ffmpeg.run`（task_id 参数，调用前检测）、`cv_tracknet.track_video` 逐采样帧前、`llm.complete_structured`（调用前 + 视频重试边界）、`highlight.find_highlights`/`report.generate_report` 透传 task_id；executor 捕获 `TaskCancelled` 走 failed 分支（error 注明「已被用户停止」）、落库后清除旗标；取消语义为「当前检查点后停止」——ffmpeg 子进程/torch 推理/LLM HTTP 无法外部硬中断，最迟当前帧/请求返回后停止；前端 TaskCard「⏹ 停止」按钮 + TaskHistory 进行中行「停止」按钮 + `api.stopTask` + store `stopTask(taskId?)`；新增 `tests/test_cancel.py` 5 用例，全量 289 测试通过

## [0.19.1] - 2026-09-24

### Fixed

- 前端跟踪历史进行中任务时，`trackTask` 先清除遗留 `store.error`（上次失败提示）再接入实时进度，避免主界面仍显示上一次失败信息；`db_service.record_task_start` 恢复续跑场景仅更新 status=processing 不清空 error（前端按 status 判断展示）

## [0.19.0] - 2026-09-24

### Added

- 预处理转码节点（`preprocess.transcode`）`height`/`fps` 参数真正生效：`VideoConfig` 新增 `resolution_height` 属性（解析 `720p/1080p/360p` 为目标高度），`preprocess.py` 的 ffmpeg scale 读 `config.video.resolution_height` 不再硬编码 720p，节点 `run()` 将 `params["height"]`/`params["fps"]` 写入节点级深拷贝 `ctx.config.video`；CV 增强工作流（id=4）`n2.params` 设 `{"height":360,"fps":30}` 使预处理与 TrackNet 推理同分辨率（360p），tracknet 段算力 ~4× 下降；新增 2 测试用例（720p 默认 + 360p 可配），全量 284 测试通过

## [0.18.2] - 2026-09-24

### Fixed

- 恢复钩子同视频去重：`main.py` 启动恢复块按 `input_md5` 分组，同一视频多个遗留非终态任务仅保留最新 1 个入队续跑，其余标记 failed（避免 N 任务并行争抢 CPU 导致 ffmpeg 预处理全部撞 300s 超时）；`recover_stale_tasks` 返回项新增 `input_md5` 字段；`record_task_start` 幂等化（task 行已存在时仅更新 status=processing、不再 INSERT，消除 UNIQUE constraint 告警）；新增 3 用例（input_md5 透出 / 同视频去重保留最新 / 上传记录复用），`test_task_recovery.py` 9 用例全绿

## [0.18.1] - 2026-09-24

### Fixed

- 服务重启后遗留的非终态任务（pending/processing/timeout）变僵尸：`main.py` 启动钩子（`init_db` 之后）扫描 DB 非终态任务，逐个处理——输入视频仍登记且物理存在（`task_outputs` kind=uploaded 反解 MD5 → `uploaded_videos` 反查 rel_path）→ 保留原 task_id/level 重新入队续跑（预处理输出已落盘，仅重做 LLM/CV 推理部分）；输入不可用 → 标记 failed 并附中断原因；恢复块仅告警不阻断启动，单任务失败不影响其他任务；新增 `tests/test_task_recovery.py` 7 用例（stale 列表/标记 failed/可恢复/不可恢复/幂等/启动钩子入队），全量 282 测试通过
- 修复恢复钩子中 `_process_one` 定义位置在 lambda 捕获之后导致 NameError（worker 线程在模块 `def _process_one` 执行前已执行 lambda）；将 `_process_one` 移至恢复块之前

## [0.18.0] - 2026-09-24

### Added

- 前端「历史任务」点击**进行中任务** → 接入主界面 TaskCard 实时进度（`store.trackTask` 复用既有 1.5s 轮询 + `workflow_nodes` 节点步骤条），完成/失败任务仍走结果弹窗

## [0.17.1] - 2026-09-24

### Fixed

- TrackNet 权重导出契约适配层（`fetch_tracknet_weights.py`）改为**固定训练分辨率 640×360 推理**（wrapper v2）：训练 `datasets.py` 与参考推理 `infer_on_video.py` 均先 `cv2.resize((640,360))` 再喂网络，节点原直通原始帧分辨率（720p）造成 4× 算力惩罚 + 球尺度分布偏移；v2 统一 `F.interpolate` 缩到 640×360，热图 (1,1,360,640) 由 `_peak_to_point` 等比映射回帧坐标（契约原生支持），去掉补零分支——实测单帧 720p 入参 42 → ~11 CPU-秒，720p 合成球视频 stride=3 定位误差均值 8.1px；同步更新 `cv_runtime.load_torch_model` 降噪 `torch.jit.load` FutureWarning

## [0.17.0] - 2026-09-24

### Added

- 新增 `backend/scripts/fetch_tracknet_weights.py` TrackNet 权重一键下载转换脚本：默认 HuggingFace 源（`vishnushenoy09/tracknet-v1-tennis`，支持 `--url` / `--from-file` 与 `HF_ENDPOINT` 镜像）→ 识别 TorchScript/state_dict/整模 pickle → 装入内置 TrackNetV1 参考结构（126 键严格对位）→ 节点契约适配（通道反序 [8..0] 对齐训练栈 (当前,前,前前)×BGR、非 8 倍数分辨率补零裁回、输出 `1−P(背景类)` 球概率热图）→ `torch.jit.script` 导出 `data/models/tracknet.pth` → 双重冒烟（常规/补零前向 + `cv_runtime.load_torch_model`）；节点级实测：合成球视频检出 26 点、定位误差均值 4px

## [0.16.1] - 2026-09-23

### Fixed

- 批次 C sidecar 改名两处回归（实机运行日志发现）：① `_transcode_for_upload` staging 文件 `.part` 无容器扩展名致 ffmpeg 推断失败（exit=234）、视频理解上传转码必回退原始文件（480p 降采样失效）→ 显式 `-f mp4` 指定容器；② `test_video_strategy_oversize_only_warns` 的 `FakePath` 缺 `Path.stem`（生产改用 `.stem` 后必报错，此前以 deselect 掩盖）→ 假对象补 `stem` 属性并恢复全量运行；新增真 ffmpeg sidecar 转码回归用例，**275 测试全绿（0 排除）**

## [0.16.0] - 2026-09-23

### Added

- 新增「CV 增强工作流」内置预设种子（方案 12 · 5.2）：`workflow_service.seed_cv_workflow`（按名幂等、`is_builtin=1`、`sort_order=1`）+ `seed_builtin_presets` 包装（默认按全表 count==0、CV 按名缺则补），`main.py` 启动接线（try/except 仅告警不阻断）
- 预置图为示例 C 降本链路：input → preprocess → detect.tracknet → post.score_highlights → edit.concat ∥ report.technical → output.artifact（无 analyze.highlight，LLM 仅出报告）；`tests/test_workflow_repo.py` 新增 5 例（R1~R10 校验/按名幂等/双种子幂等/存量库门控/内置保护），全量 273 通过

## [0.15.1] - 2026-09-23

### Added

- 新增 `tests/test_workflow_graph.py::test_optional_dead_source_does_not_kill_artifact` 回归用例（report optional 失败场景下 artifact 真实投影断言），全量 268 测试通过

### Fixed

- 级联跳过死亡标记收口：`on_failure=skip` 节点保持级联锚点（自身与下游 skipped、任务仍成功），`optional=True` 节点失败仅标记 skipped、**不再记入死亡集合**——修复 report（契约「失败不致命，保留集锦」）失败时下游 `output.artifact` 被级联跳过、已生成的集锦/高光产物不投影的缺陷（方案 12 · 5.2 预置图设计时发现）

## [0.15.0] - 2026-09-23

### Added

- CV 感知层 6 节点（方案 12 · Step 2）：`detect.tracknet` / `detect.court` / `detect.player` / `detect.pose` / `post.score_highlights` / `post.classify_strokes`，内置节点 9 → 15；4 个 detect 节点 `on_failure="skip"` 级联降级、2 个 post 节点保持 fail（方案 4.3.3/2.8 契约）
- 新增 `app/utils/cv_*.py` 六个推理薄封装 + `cv_runtime.py` 共享运行时（依赖探测 `require`、权重解析 `resolve_weights`、TorchScript/整模加载、统一抽帧 `sample_frames`；依赖/权重缺失抛 `CvUnavailable`），模块级导入零重依赖、无 CV 环境节点注册与 schema 照常工作
- `event_detect` 新增公共聚合 `track_to_candidates`（轨迹点→回合窗口，复用 `_cluster_hits`）与 `scores_to_candidates`（运动分数→窗口，复用 `_windows_from_mask`），新旧检测信号共用同一套候选聚合；原候选段转换提取为 `_windows_to_segments`
- `pyproject.toml` 新增 `cv` 可选依赖组（`uv sync --extra cv`：torch / ultralytics / mediapipe / transformers / pillow / opencv）；权重约定 `backend/data/models/`（gitignore 忽略）
- 新增 `tests/test_workflow_cv_nodes.py` 24 用例（schema 15 节点、端口与失败策略契约、参数边界、R4/R5/R8 图校验、节点传参与函数式传递、dtw 模板分类可测路径、示例 B 并联对比图校验、示例 C 图端到端、CvUnavailable skip 级联），全量 253 测试通过

### Changed

- 工作流执行器改为 Kahn 分层（`topo_levels`）执行：同层节点按 `spec.stage` 分组——组间串行（`result.stage` 标量字段阶段上报顺序确定），组内同阶段节点线程池并行（如 `detect.*` 同为 detecting、双 analyze 分支同为 highlighting）；单节点组内联调用保持链式图串行语义，准备/收尾在主线程按 node.id 升序（日志与状态确定可复现）（方案 12 · 批次 C1）
- 节点落库改声明式（`NodeSpec.persists` / `records_input`）：`preprocess.transcode` 新增 `meta` 输出端口透出 probe 元信息，`preprocess.transcode`/`edit.concat`/`report_technical` 移除 `db_service` 直调、保持纯函数，执行器收尾阶段主线程统一调 `record_task_input`/`record_task_output`（并行执行期零 DB 写，规避 SQLite 多线程写锁风险）；`_transcode_for_upload` 上传转码缓存改 sidecar 临时文件 + `os.replace` 原子落位（同 stem 并行分支不再互踩）（方案 12 · 批次 C2）
- 新增 `tests/test_workflow_parallel.py` 14 用例（topo_levels 分层/排序/环/禁用、Barrier 并行实证、同层致命失败收尾语义、链式内联、声明式落库契约与纯函数守卫、端到端落库），全量 267 测试通过

## [0.14.1] - 2026-09-23

### Added

- 新增 `tests/test_config_legacy_keys.py` 6 用例（旧键回退/新键优先/列表透出/写迁移/默认值清行/双键删除）+ `test_pipeline.py` 2 用例（可用模型段保留、all 档兜底切满全程），全量 229 测试通过

### Fixed

- 均匀切片兜底改为"先过滤准备段、仅在无可用高光时触发"：训练类视频模型产出可用段（如 rally 6.6–114.9）不再被无条件覆盖，全量拼接（`edit.concat all_mode`）不再缩水成 15 秒
- 均匀切片按档位定长：all 档位每段 = duration/count 切满全程；普通档位维持 target_duration/count 均分散布
- 配置键对齐（b352955 重命名遗留的 DB 孤儿行）：`llm.analysis_level`/`llm.highlight_strategy` 读取回退旧键 `highlight.level`/`llm.analysis_mode`，写入时孤儿行原地迁移、删除时双键清理；前端 `stores/task.js` 同步读取新键（旧键兼容保留）
- AGENTS.md 契约同步：`/api/v1/config` pipeline 类键名更正；移除已删除的 StrategyModal 过期引用（d29dfae）

## [0.14.0] - 2026-09-23

### Added

- 阶段 1 Prompt 增强（方案 12 · Step 1）：`report_templates` 教学知识库 3 条/级 → 21 条/级，分 basic/standard/expert 档累积注入（`knowledge_for`）
- 新增 `prompts/tennis_domain.py` 网球规则/生物力学领域知识模块：`RULES` + `BIOMECHANICS` 六章按档位缩放，`label_reference` 提供高光标签判分判据
- `build_highlight_prompt` 增强：视觉判别规则（7 类画面特征区分）+ few-shot 示例 4 则 + 标签判据注入；新增 `prompt_variant` 变体参数（standard/strict/teaching，非法值回落 standard）
- `analyze.highlight` 节点新增 `prompt_variant` ParamSpec 并透传至 `find_highlights`；`report.technical` 节点新增 `knowledge_level` ParamSpec 并透传至 `generate_report`
- `build_report_prompt` 按 `knowledge_level` 注入教学要点库与领域知识，报告诊断优先引用注入内容
- 新增 `tests/test_prompt_knowledge.py` 10 个用例（知识库分档/领域模块/变体/参数校验/schema/透传），全量 221 测试通过

## [0.13.0] - 2026-09-23

### Added

- 工作流独立性加固（方案 12 · Step 0 批次 A）：执行器每节点深拷贝 config 隔离改写；端口值函数式传递（`model_copy`）禁止原地修改上游输出；运行时端口类型校验（`PORT_PY_TYPES` / `validate_port_value`，store 与 resolve 双向），并预登记 `track`/`court`/`pose` 端口类型
- 节点失败策略 `NodeSpec.on_failure`（`fail` 默认 / `skip`）：skip 节点失败后自身与下游级联标记 skipped、任务保持成功，为 CV 感知层节点降级铺路
- 节点自动发现注册：`nodes/__init__.py` 改为 `pkgutil` 扫描，新增节点放文件即注册

### Fixed

- 预置工作流图补连 `highlight → output.artifact`（此前依赖执行器隐式兜底投影掩盖）；移除兜底扫描，改由 `validate()` 校验期 R8 检查缺连（有高光生产节点时阻断）

## [0.12.0] - 2026-09-21

### Added

- 主页面左右等宽两列布局（上传 + 任务），去掉简易模式与策略弹窗
- UploadPanel 工作流下拉框：列出所有启用工作流，选中即激活，底部「编辑工作流...」打开画布
- TaskCard 步骤条动态化：从 `task.workflow_nodes` 读取节点列表，与工作流一一对应
- 工作流执行器逐节点进度上报（`WorkflowNodeProgress` 模型）
- 模型参数 `provider/model` 格式路由：自动解析服务商前缀，动态切换 base_url + api_key
- `GET /api/v1/tasks` 数据库 fallback：任务完成后队列丢失仍可查询

### Fixed

- 工作流无 `detect.candidates` 节点时不再偷偷跑信号检测（`_NO_CANDIDATES` sentinel 区分调用来源）
- `test_db_service_roundtrip` 测试结束后恢复原始数据库引擎，修复 upload 测试 500 错误

## [0.11.0] - 2026-09-20

### Added

- 任务历史列表与详情弹窗：头部新增「历史任务」按钮，右侧滑出面板展示最近 50 条处理记录（状态徽章 / 文件名 / 层级 / 耗时 / 创建时间），支持按状态筛选；点击已完成任务弹出详情弹窗，可播放集锦视频、查看分析报告、下载文件

### Fixed

- 历史任务详情从数据库读取（`GET /api/v1/db/tasks/{task_id}`），不再依赖内存队列，服务重启后历史记录仍可查看完整结果；视频/报告文件服务增加数据库兜底路径
- 详情弹窗视频显示逻辑：有视频文件路径即渲染播放器，不依赖 highlight/report JSON 是否存在

## [0.10.0] - 2026-09-20

### Added

- 工作流执行集成：`_process_one` 检查 `workflow.default_graph_id`，有激活工作流时自动走 `Executor.execute()`，否则回落旧 `run_pipeline`
- `edit.concat` 节点新增 `highlight` 透传输出端口，供下游节点消费高光数据
- `analyze.highlight` 节点新增 `model` 参数，支持从数据库服务商表动态选择 LLM 模型
- 执行器兜底投影：若 `output.artifact` 未收到 `highlight` 输入，自动从上下文输出中查找并注入
- 前端工作流激活：画布编辑器顶部「⚡ 激活」按钮 + 预设列表悬停激活 + 成功提示
- 09 方案文档 v2.0.0：新增工作流使用说明（快速上手/对比表/执行优先级/连接规则/FAQ）

### Fixed

- `video_editor.py` 修复 ffmpeg `-t` 标志位置：从 `-i` 之后移到 `-i` 之前，修复 `Error opening input file -t`
- `TaskCard.vue` 修复工作流模式下结果不显示：`showResults` 增加 `highlight_video_path` 兜底检查
- `workflow store` 修复 `newDraft` 加载已保存工作流时 `graph` 为 undefined 的崩溃：改为 async 并通过 `api.getWorkflow(id)` 获取完整图数据

## [0.9.0] - 2026-09-19

### Added

- 可编排工作流系统（ComfyUI 风格 DAG）：节点契约与注册表（`workflow/spec.py`）、9 个内置节点（input.video / preprocess.transcode / detect.candidates / analyze.highlight / post.filter_segments / post.uniform_slices / edit.concat / report.technical / output.artifact）、图结构校验（R1~R10 十项规则）与 Kahn 拓扑排序、执行器（`workflow/executor.py`）含 Context 传值与产物投影
- 预置工作流编译器 `workflow/presets.py`：`compile_from_legacy(stages, strategy, level)` 将旧管线三元组编译为 WorkflowGraph，`compile_from_config(db, config)` 读取配置 KV 编译
- 工作流持久化：`workflows` 表（name/graph_json/is_builtin/enabled）+ Alembic 迁移 + `workflow_service.py` CRUD/激活/种子
- 工作流 API：`GET /api/v1/workflows/schema`（节点目录）、`GET/POST/PUT/DELETE /api/v1/workflows`（CRUD）、`POST /{id}/activate`、`POST /validate`（草稿校验不落库）
- 前端工作流编排面板（`WorkflowPanel.vue`）：右侧滑出面板，含预设列表、节点目录、参数表单、JSON 导入导出；App.vue 头部新增「工作流」按钮
- 配置 KV 新增 `workflow.mode`（legacy/workflow）、`workflow.default_graph_id`；重命名 `highlight.level` → `llm.analysis_level`、`llm.analysis_mode` → `llm.highlight_strategy`
- 新增 `segment_ops.py` 抽取 `_exclude_prep` / `_clamp_segments` / `_uniform_slices` 供新旧代码共享
- 分析分层 `level` 驱动高光选择策略：`beginner`/`intermediate`/`professional` 各对应不同高光数量档位（`max_segments` 2/3/5），由 `HighlightConfig.level_strategies` 映射 + `resolve_selection` 统一解析
- 新增「所有高光回合」档位 `level=all`：遍历整段视频、截取并拼接全部高光时刻，不受 `max_segments` 与 `target_duration` 约束；`HighlightResult.all_highlights` 作为全段拼接唯一事实来源，下游剪辑据此全段拼接，且 `core.run_pipeline` 在 all 档位跳过技术分析报告生成（仅剪辑、不分析）
- `event_detect.detect_candidates` 新增 `top_n` 参数（`None`=不截断），`build_highlight_prompt` 新增 `select_all` 全量返回约束，前端 `UploadPanel` 新增「所有高光回合」选项
- `POST /api/v1/process` 的 `level` 参数新增合法值校验（beginner/intermediate/professional/all，非法返回 400）

## [0.8.0] - 2026-09-18

### Added

- 视频上传实现「两步上传 + MD5 秒传」：前端增量计算文件 MD5 后先预检，命中则跳过上传、复用已落盘视频重新分析；未命中走 5MB 分片上传 + 断点续传（单片 crc32 校验、整文件 MD5/size 二次校验），按 `{md5}{ext}` 去重落盘
- 新增上传接口 `POST /api/v1/upload/check`（MD5 预检）、`POST /api/v1/upload/chunk`（分片）、`GET /api/v1/upload/chunks`（进度）、`POST /api/v1/upload/complete`（合并登记）
- `POST /api/v1/process` 新增可选 `md5` 字段：引用已上传视频实现秒传复用；保留原 `file` 整体直传兜底
- 新增 `uploaded_videos` 表（MD5 主键去重目录），配套 Alembic 迁移；前端 `UploadPanel` 展示「秒传命中 / 上传进度 / 分析中」状态

## [0.7.0] - 2026-09-17

### Added

- 模型服务商管理对齐 TennisDiary：激活服务商（`ai.provider`）与选定模型（`ai.model`）改由 `system_config` 配置 KV 覆盖，`get_ai_config` 统一解析；`model_providers` 重构为 `ai_providers`（id 主键、enabled 用 int、去除 is_active/selected_model）
- 新增 `check-models` 模型可用性校验接口（GET /models 清单优先，否则逐模型 chat/completions 探测），前端弹窗逐模型显示 ✓/✗
- 服务商增删改路由改按整数 id；删除被 `ai.provider` 直选引用的服务商返回 409；API Key 掩码改为前 3 + 末 4 位
- 新增 `/api/v1/config` 配置端点（列表/覆盖/恢复），`/health`、LLM 调用、启动自检均经配置解析，DB 不可用时回落静态配置
- 前端服务商管理弹窗（表格 + 内联多模型表单 + 逐模型校验）与上传面板配置直选下拉

## [0.6.0] - 2026-09-16

### Added

- 根目录搭建 VitePress 文档站：新增根 `package.json` 与 `vite.config.js`，文档内容根指向 `docs/`；`docs:dev`/`docs:build`/`docs:preview` 脚本可用；`docs/.vitepress/config.mts` 配置 `host`/`allowedHosts` 与 `ignoreDeadLinks`，新增 `references/`、`guides/` 分区首页
- 任务异步化重构：上传文件后立即创建任务并返回 `task_id`；任务交由后台 `TaskQueue` 线程池异步执行管线（预处理 → 高光识别 → 剪辑合成 → 技术分析报告），逐阶段落库
- 前端按 `task_id` 轮询任务状态；任务异常（如 FFMPEG 不可用）即终止并回填 `error` 字段，前端展示具体错误而非持续 500 轮询

## [0.5.1] - 2026-09-16

### Fixed

- 修复日志端到端测试（TC-04）在完整测试套件下偶发失败：第三方库（uvicorn 等）在导入期可能将 `uvicorn.access` 等 logger 的 `disabled` 置为 `True` 或调用 `logging.disable(...)`，导致标准 logging 被静默禁用、`InterceptHandler` 收不到日志。`app/utils/logger.py` 的 `_intercept_stdlib_logging` 接管时复位 `logging.disable(0)` 及被接管 logger 的 `level`/`disabled`；`tests/test_logging.py` 的 `_reset_logging` 夹具新增 `_reset_stdlib`，在每个用例前后重置标准库 logging 全局状态，实现真正隔离

## [0.5.0] - 2026-09-16

### Added

- 启动环境自检：服务启动时对 FFMPEG 安装、数据库连接、模型提供商配置、数据目录可写做四项检查，失败仅告警、不阻断启动
- `/health` 新增 `environment` 字段，返回启动自检明细（ffmpeg / database / provider / data_dir，status 为 ok/warn/fail）
- 收口 `/health` 契约：原顶层 `ffmpeg` / `database` 字段已并入 `environment`（开发阶段不做向后兼容）；`provider` 改为嵌套对象 { name / model / base_url / api_key_set / source }，`source` 标识生效来源（`database` 或 `config`）
- 生效提供商统一以数据库 `model_providers` 的 `is_active` 记录为准：运行时 LLM 调用、`/health`、启动自检均优先读 DB 生效记录（`db_service.get_active_provider()`），使 `/api/v1/db/providers/{name}/activate` 的切换真正生效；数据库不可用时回退静态 `config.active_provider`

### Changed

- 模型提供商种子写入改用 `init_db` 已持有的 `config`：去掉 `db_models._seed_providers` 内部重复的 `load_config()`，避免多重配置来源下 `is_active` 判定以磁盘 `config.yaml` 为准而漂移；`db.py` 的 `init_db(engine)` 路径仍就地 `load_config()` 后传入

## [0.4.1] - 2026-09-16

### Changed

- 重构测试环境隔离：`load_config()` 不再硬编码测试分支与 `data_test` 兜底，改为通用 `.env.<env>` 约定（`TENNISCLIP_ENV=test` → `.env.test`）；测试数据目录兜底（未声明 `TENNISCLIP_DATA_DIR` 时默认 `data_test`）移至 `backend/tests/conftest.py` 初始化阶段，公共代码保持环境无关

## [0.4.0] - 2026-09-15

### Added

- 引入 `.env.test` 测试配置隔离：设置 `TENNISCLIP_ENV=test` 后 `load_config()` 自动加载 `backend/.env.test`，与开发/生产配置彻底隔离
- 测试数据统一落入 `backend/data_test/`（由 `.env.test` 的 `TENNISCLIP_DATA_DIR=data_test` 驱动），绝不触碰真实 `backend/data/`
- 新增 `backend/tests/conftest.py`：pytest 启动时设置 `TENNISCLIP_ENV=test` 并预建 `data_test/` 目录
- 仅提交 `backend/.env.test.example` 模板，`.env.test` 不入库（`.gitignore` 忽略 `backend/.env.test` 与 `backend/data_test/`）
- 新增文档 `docs/plans/03-测试环境隔离方案.md`

## [0.3.0] - 2026-09-15

### Added

- 新增环境变量 `TENNISCLIP_DATA_DIR`：覆盖数据目录（`paths.data_dir`），优先级最高，可整体迁移数据库/输入/输出/日志

## [0.2.0] - 2026-09-15

### Added

- 后端接入 loguru 日志系统：统一 `时间 | 级别 | 模块:函数:行号 - 消息` 格式，控制台（彩色）+ 滚动文件（`data/app.log`，rotation=10MB / retention=7d / compression=zip）
- 接管 uvicorn / FastAPI 标准 logging（InterceptHandler），全链路同一套日志格式
- 日志级别来源：`TENNISCLIP_LOG_LEVEL` 环境变量 > `config.yaml` 的 `logging.level` > 默认 `INFO`
- 新增 `backend/app/utils/logger.py`（基于 loguru），27 处调用点迁移至 `get_logger(__name__)` + `{}` 延迟求值占位符

### Changed

- 目录布局调整：输入（sample_videos）、输出（outputs）、日志（app.log）、数据库（tennisclip.db）统一归入 `backend/data/`（`data_dir`），`.gitignore` 简化为整体忽略 `backend/data/`

## [0.1.5] - 2026-07-08

### Added

- 后端数据迁移改用 Alembic：`init_db` 服务启动时自动升级，旧库 `create_all` 兜底并 `stamp head`
- 新增 `backend/alembic/` 迁移目录（`env.py` 动态读 `DATABASE_URL`/`config.yaml`）与初始迁移 revision（6 张表）
- 文档同步更新 Alembic 迁移说明（README / AGENTS / `.env.example`）

### Changed

- `app/db.py` 与 `app/services/db_service.py` 的建表逻辑由裸 `create_all` 改为「Alembic 优先 + create_all 兜底」

## [0.1.4] - 2026-07-08

### Fixed

- 修复 VideoPlayer.vue 重复 `</script>` 标签导致 Vite 编译报错「Invalid end tag」

## [0.1.3] - 2026-07-08

### Added

- 部署模式：开发环境 Vite dev server 代理后台，生产环境前后端分开部署
- 前端 API 客户端支持 `VITE_API_BASE_URL` 构建注入后端基地址（跨域部署）
- 后端 FastAPI 新增可配置 CORS 中间件（`cors.allowed_origins`，默认 `*`，可被 `CORS_ALLOWED_ORIGINS` 覆盖）
- 文档同步更新部署模式与前后端环境变量说明（README / AGENTS）

## [0.1.2] - 2026-07-08

### Added

- 新增根目录 `AGENTS.md`，为 AI 编码代理提供项目上下文、编码约定与边界

## [0.1.1] - 2026-07-08

### Added

- 新增 MIT 开源协议（`LICENSE`），并在 README 中说明许可信息

## [0.1.0] - 2026-07-08

### Added

- 初始化 TennisClip AI 前后端分离项目结构与文档体系
- 新增后端 FastAPI 服务、命令行入口、数据库模型与测试
- 新增前端 Vue 3 / Vite / Pinia / Tailwind 页面与组件
- 集成 docs-manage 与 git-commit 项目 skills
- 添加 StepFun / OpenAI 兼容 provider 配置与 mock 回退能力
- 提供 SQLite / PostgreSQL / MySQL 多数据库适配与自动落库
- 增加高光识别、自动剪辑、结构化动作技术报告生成流水线
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  