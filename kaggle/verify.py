#!/usr/bin/env python3
"""Kaggle GPU 验证入口：在 Kaggle Notebook 上完整移植并运行「CV 增强调试工作流」。

设计原则（对齐方案 14 / AGENTS.md 独立性契约）：
- **不复制业务代码**：本脚本通过 ``--repo`` 指向解压根（含 ``app/`` + ``prompts/`` 两个
  顶层包），把该根加入 sys.path，``import app.workflow...`` / ``import prompts...`` 直接跑
  16 节点 executor 图，代码零分叉。
- **纯计算路径**：不连数据库。executor 收尾阶段的 ``db_service.record_task_*``
  调用需数据库，故本脚本**不走 executor.execute()**（它调 db_service），
  而是复刻 executor 的「分层并行 + Context 传值 + 级联跳过 + 产物投影」核心
  逻辑（约 150 行），落库部分替换为写 JSON 文件到 /kaggle/working/。
  这样验证的是「节点算法 + 图调度 + 降级契约」的正确性，而非 DB 持久化。
- **权重/视频走 dataset**：Kaggle 上路径固定为 /kaggle/input/{slug}/，
  本脚本用 ``--input`` 指向 dataset 根，``cv_runtime.MODELS_DIR`` 指向 ``weights/``
  （使 ``resolve_weights`` 缺省命中 ``tracknet.pth``），``data/*.mp4`` 自动发现。

Kaggle 侧运行（notebook cell）：
    !pip install -q loguru pydantic pyyaml python-dotenv numpy opencv-python-headless
    !pip install -q torch torchvision   # 权重若需 CLIP 再加 transformers
    # 解压 backend/{app,prompts} → /kaggle/working/（--repo 指向解压根）
    tar -xzf /kaggle/input/{slug}/app.tar.gz -C /kaggle/working
    python /kaggle/input/{slug}/verify.py \
        --repo /kaggle/working \
        --input /kaggle/input/{slug} \
        --out /kaggle/working \
        --device cuda

本地 dry-run（无 GPU/无真实权重，验证 import 闭包 + 图编译 + PureExecutor 构建）：
    python kaggle/verify.py --dry-run --repo backend --input kaggle/dataset --out kaggle/dataset/out

依赖闭包（全部随 backend/{app,prompts} 打包，Kaggle 上 pip 装第三方即可）：
    loguru / pydantic / pyyaml / python-dotenv / numpy / cv2 / torch /
    ultralytics（球员框，show_players 时）/ transformers（CLIP 评分时）
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径准备：把仓库 backend/ 加入 sys.path，使 `import app.*` 可用
# ---------------------------------------------------------------------------

def _boot_repos(repo: Path) -> None:
    """把 repo（解压根，含 app/ + prompts/ 两个顶层包）加入 sys.path 头部。

    本地：--repo backend（含 backend/app/ + backend/prompts/）
    Kaggle：--repo /kaggle/working（app.tar.gz 解压后含 app/ + prompts/）
    """
    repo = repo.resolve()
    if not (repo / "app").is_dir():
        raise SystemExit(f"[verify] --repo 指向 {repo}，但未找到 app/ 子目录")
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))


# ---------------------------------------------------------------------------
# 复刻 executor 核心（纯计算，无 db_service）
# ---------------------------------------------------------------------------

class PureExecutor:
    """Kaggle 侧执行器：与 app.workflow.executor.Executor 同构，落库改为写 JSON。

    移植自 executor.execute() 的「Kahn 分层 + 同层 stage 分组并行 + 级联跳过 +
    产物投影」逻辑，区别仅在：
    - 不调 db_service.record_task_*（无数据库），收尾产物写 {out}/{task_id}/result.json；
    - task_out 直接由调用方传入（Kaggle 上为 /kaggle/working/{task_id}/）。
    """

    _MAX_LEVEL_WORKERS = 8

    def __init__(self, graph, config, result, level: str = "intermediate", task_out: Path = None):
        from app.workflow.executor import Context
        self.graph = graph
        self.config = config
        self.result = result
        self.level = level
        self.task_out = task_out or (config.ensure_output_dir() / result.task_id)
        self.task_out.mkdir(parents=True, exist_ok=True)
        self._artifact_records: dict[str, list] = {}
        self._dead_nodes: set[str] = set()   # 级联锚点集合（on_failure=skip 失败节点 + 级联跳过节点）
        self._Context = Context

    def _run_node(self, fn, node_ctx, params):
        try:
            return ("ok", fn(node_ctx, params))
        except Exception as exc:  # noqa: BLE001
            return ("err", exc)

    def execute(self, video_path: Path):
        from app.utils import cancel
        from app.workflow.spec import get_fn, get_spec, resolve_params
        from app.workflow.executor import Context

        task_id = self.result.task_id
        self.task_out.mkdir(parents=True, exist_ok=True)
        ctx = Context(
            video_path=video_path,
            config=self.config,
            task_out=self.task_out,
            task_id=task_id,
            level=self.level,
            result=self.result,
        )

        # ---- 初始化节点进度（同 executor）----
        levels = self.graph.topo_levels()
        order = [nid for level in levels for nid in level]
        self.result.workflow_nodes = []
        node_index_map = {}
        for i, node_id in enumerate(order):
            node = self.graph.nodes[node_id]
            spec = get_spec(node.type)
            self.result.workflow_nodes.append(_NodeProgress(
                node_id=node_id, node_type=node.type,
                label=spec.label if spec else node.type,
                stage=spec.stage if spec else "unknown", status="pending",
            ))
            node_index_map[node_id] = i

        dead_nodes: set[str] = set()
        skipped: list[str] = []
        started = time.monotonic()
        # 主循环级联判定与 _cascade_skip 共享同一集合
        self._dead_nodes = dead_nodes

        for level in levels:
            cancel.check_cancelled(task_id, "workflow 节点边界")

            runnable = []
            for node_id in level:
                node = self.graph.nodes[node_id]
                if not node.enabled:
                    continue
                spec = get_spec(node.type)
                if spec is None:
                    raise RuntimeError(f"未知节点类型：{node.type}")
                fn = get_fn(node.type)
                if fn is None:
                    raise RuntimeError(f"节点 {node_id}({node.type}) 无执行函数")

                inputs = ctx.resolve_inputs(self.graph, node_id)

                # 级联跳过（批次 B1，同 executor）
                should_skip, skip_reason = False, ""
                for port, value in inputs.items():
                    if value is not None:
                        continue
                    in_edges = [e for e in self.graph.edges
                                 if e.to_node_id == node_id and e.to_port == port]
                    if not in_edges:
                        continue
                    dead_sources = [e.from_node_id for e in in_edges
                                    if e.from_node_id in dead_nodes]
                    if len(dead_sources) == len(in_edges):
                        should_skip, skip_reason = True, f"上游 {', '.join(sorted(dead_sources))} 未产出 {port}"
                        break
                if should_skip:
                    self.result.workflow_nodes[node_index_map[node_id]].status = "skipped"
                    skipped.append(node_id)
                    dead_nodes.add(node_id)
                    continue

                params = resolve_params(spec.params, node.params)
                node_ctx = Context(
                    video_path=ctx.video_path,
                    config=copy.deepcopy(ctx.config),   # 批次 A1：节点级独立 config
                    task_out=ctx.task_out,
                    task_id=ctx.task_id,
                    level=ctx.level,
                    result=ctx.result,
                    inputs=inputs,
                )
                runnable.append((node_id, spec, fn, params, node_ctx, node_index_map[node_id]))

            # 同层按 spec.stage 分组：组间串行、组内线程池并行（批次 C1，同 executor）
            stages: dict[str, list] = {}
            for item in runnable:
                stages.setdefault(item[1].stage, []).append(item)
            for stage, items in stages.items():
                for item in items:
                    self.result.workflow_nodes[item[5]].status = "running"
                if len(items) == 1:
                    self._execute_one(items[0])
                else:
                    with ThreadPoolExecutor(max_workers=self._MAX_LEVEL_WORKERS) as pool:
                        futures = {pool.submit(self._run_node, i[2], i[4], i[3]): i for i in items}
                        for fut in as_completed(futures):
                            self._settle(fut, futures[fut])

            # 收尾：on_failure=skip 失败节点已由 _cascade_skip 加入 dead_nodes；
            # 级联跳过的节点（should_skip 分支）已在主循环加入 dead_nodes。

        self.result.elapsed_seconds = round(time.monotonic() - started, 3)
        self._persist_result()
        return self.result

    def _execute_one(self, item):
        node_id, spec, fn, params, node_ctx, idx = item
        status, out = self._run_node(fn, node_ctx, params)
        self._settle_result(node_id, spec, node_ctx, status, out, idx)

    def _settle(self, fut, item):
        status, out = fut.result()
        self._settle_result(item[0], item[1], item[4], status, out, item[5])

    def _settle_result(self, node_id, spec, node_ctx, status, out, idx):
        progress = self.result.workflow_nodes[idx]
        if status == "ok":
            progress.status = "done"
            ctx = node_ctx
            ctx.store(node_id, out, {p.name: p.type for p in spec.outputs})  # p.type 已是字符串（PortType 常量）
            self._project_artifacts(spec, out, ctx)
        else:
            # 失败：on_failure=skip → skipped + 级联；否则 failed（对齐 executor 的 skip 策略）
            if spec.on_failure == "skip":
                progress.status = "skipped"
                self._cascade_skip(node_id, spec, node_ctx, str(out))
            else:
                progress.status = "failed"
                progress.error = str(out)
                self.result.error = f"节点 {node_id}({spec.type}) 失败：{out}"
                from app.models import TaskStatus
                self.result.status = TaskStatus.FAILED
                raise SystemExit(f"[verify] 节点 {node_id} 失败（on_failure=fail），终止")

    def _cascade_skip(self, node_id: str, spec, node_ctx, reason: str = ""):
        """on_failure=skip 节点失败：自身 + 下游必填输入缺失的级联跳过（对齐 executor 批次 B1）。

        级联规则：下游节点若某必填输入的所有来源都在 dead_nodes（= 当前失败节点 + 已级联节点），
        则该下游也标 skipped 并入 dead_nodes。post.visualize_track 是终端节点无下游，仅自身 skipped。
        """
        from app.utils.logger import get_logger
        logger = get_logger(__name__)
        # 标记当前节点死亡（下游 resolve_inputs 时按 dead_nodes 级联）
        self._dead_nodes.add(node_id)
        logger.info("verify: 节点 {}({}) skipped —— 级联锚点，原因={}",
                    node_id, spec.type, (reason or "on_failure=skip")[:120])

    def _project_artifacts(self, spec, outputs, ctx):
        """产物投影（批次 B2：仅信任 output.artifact 显式连线，无兜底扫描）。

        与 executor._record_outputs 同义，但落 JSON 而非 db_service.record_task_output。
        """
        for port, kind in (spec.persists or ()):
            value = (outputs or {}).get(port)
            if value is None:
                continue
            if isinstance(value, (str, Path)):
                file_path = Path(value)
            else:
                file_path = Path(ctx.task_out) / f"{kind}.json"
            if not file_path.is_file():
                continue
            self._artifact_records.setdefault(kind, []).append(
                {"file": str(file_path), "size_mb": round(file_path.stat().st_size / (1024*1024), 3)}
            )

    def _persist_result(self):
        """把 TaskResult + 产物记录 + 节点进度写到 {task_out}/result.json。"""
        from app.models import TaskResult
        record = {
            "task_id": self.result.task_id,
            "status": self.result.status.value if hasattr(self.result.status, "value") else str(self.result.status),
            "error": self.result.error,
            "elapsed_seconds": self.result.elapsed_seconds,
            "highlight_video_path": self.result.highlight_video_path,
            "highlight": self.result.highlight.model_dump() if self.result.highlight else None,
            "report": self.result.report.model_dump() if self.result.report else None,
            "workflow_nodes": [
                {"node_id": n.node_id, "node_type": n.node_type, "label": n.label,
                 "stage": n.stage, "status": n.status}
                for n in (self.result.workflow_nodes or [])
            ],
            "artifacts": self._artifact_records,
            "out_dir": str(self.task_out),
        }
        out_file = self.task_out / "result.json"
        out_file.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[verify] 结果已落盘：{out_file}")
        print(f"[verify] 状态={record['status']} 耗时={record['elapsed_seconds']}s "
              f"节点进度={[n['status'] for n in record['workflow_nodes']]}")


# 节点进度（与 WorkflowNodeProgress 同字段，纯 dict 化便于 JSON 落盘）
class _NodeProgress:
    """节点进度（与 WorkflowNodeProgress 同字段，纯 dict 化便于 JSON 落盘）。"""
    def __init__(self, node_id, node_type, label, stage, status="pending", error=None):
        self.node_id, self.node_type = node_id, node_type
        self.label, self.stage, self.status = label, stage, status
        self.error = error


# ---------------------------------------------------------------------------
# 图编译：从 dataset 内的 _cv_debug_graph_dict JSON 还原（与 workflow_service 一致）
# ---------------------------------------------------------------------------

def build_cv_debug_graph() -> "WorkflowGraph":
    """还原「CV 增强调试工作流」图（含 post.visualize_track 诊断分支）。

    与 workflow_service._cv_debug_graph_dict() 完全一致（节点/边/参数），
    保证验证的是线上正在用的那张图，而非另写一份。
    """
    # 导入节点包触发 @register 注册（app.workflow.nodes.__init__ 自动发现）
    import app.workflow.nodes  # noqa: F401
    from app.workflow.graph import WorkflowGraph

    graph_dict = {
        "version": 1,
        "name": "CV 增强调试工作流",
        "nodes": [
            {"id": "n1", "type": "input.video", "params": {}, "enabled": True},
            {"id": "n2", "type": "preprocess.transcode", "params": {}, "enabled": True},
            {"id": "n3", "type": "detect.tracknet", "params": {}, "enabled": True},
            {"id": "n4", "type": "post.score_highlights", "params": {}, "enabled": True},
            {"id": "n5", "type": "edit.concat", "params": {}, "enabled": True},
            {"id": "n6", "type": "report.technical", "params": {}, "enabled": True},
            {"id": "n7", "type": "output.artifact", "params": {}, "enabled": True},
            {"id": "n8", "type": "post.visualize_track", "params": {}, "enabled": True},
        ],
        "edges": [
            {"id": "e1", "from": ["n1", "video"], "to": ["n2", "video"]},
            {"id": "e2", "from": ["n2", "video"], "to": ["n3", "video"]},
            {"id": "e3", "from": ["n2", "duration"], "to": ["n3", "duration"]},
            {"id": "e4", "from": ["n3", "candidates"], "to": ["n4", "candidates"]},
            {"id": "e5", "from": ["n2", "video"], "to": ["n4", "video"]},
            {"id": "e6", "from": ["n4", "highlight"], "to": ["n5", "highlight"]},
            {"id": "e7", "from": ["n2", "video"], "to": ["n5", "video"]},
            {"id": "e8", "from": ["n4", "highlight"], "to": ["n6", "highlight"]},
            {"id": "e9", "from": ["n2", "video"], "to": ["n6", "video"]},
            {"id": "e10", "from": ["n5", "video"], "to": ["n7", "video"]},
            {"id": "e11", "from": ["n5", "highlight"], "to": ["n7", "highlight"]},
            {"id": "e12", "from": ["n6", "report"], "to": ["n7", "report"]},
            {"id": "e13", "from": ["n2", "video"], "to": ["n8", "video"]},
            {"id": "e14", "from": ["n3", "track"], "to": ["n8", "track"]},
            {"id": "e15", "from": ["n2", "duration"], "to": ["n8", "duration"]},
        ],
    }
    graph = WorkflowGraph.from_dict(graph_dict)
    graph.validate()
    return graph


# ---------------------------------------------------------------------------
# 权重/视频发现（Kaggle dataset 布局）
# ---------------------------------------------------------------------------

def find_weights(input_root: Path) -> Path:
    """在 dataset 的 weights/ 下找 tracknet.pth。"""
    cand = input_root / "weights"
    pth = list(cand.glob("*.pth")) + list(cand.glob("*.pt")) if cand.is_dir() else []
    if not pth:
        raise SystemExit(f"[verify] 未找到 TrackNet 权重：{cand}/*.pth")
    return pth[0]


def find_videos(input_root: Path) -> list[Path]:
    vids = list((input_root / "data").glob("*.mp4")) if (input_root / "data").is_dir() else []
    return sorted(vids)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Kaggle GPU 验证：完整移植并运行 CV 增强调试工作流")
    ap.add_argument("--repo", required=True, help="解压根目录（含 app/ + prompts/ 两个顶层包；本地=backend/，Kaggle=/kaggle/working）")
    ap.add_argument("--input", required=True, help="Kaggle dataset 根（含 weights/ data/）")
    ap.add_argument("--out", default="/kaggle/working", help="产物输出根目录")
    ap.add_argument("--device", default="cuda", help="推理设备 cuda|cpu|auto")
    ap.add_argument("--video", default="", help="指定单个视频（缺省跑 data/ 下全部 mp4）")
    ap.add_argument("--dry-run", action="store_true",
                    help="仅验证 import 闭包 + 图编译 + PureExecutor 构建，不跑逐帧推理（本地无 GPU 沙箱用）")
    args = ap.parse_args()

    # 受限环境（Windows 沙箱 dry-run）关 loguru enqueue（避免 multiprocessing 管道创建失败）
    import os
    if args.dry_run or sys.platform == "win32":
        os.environ.setdefault("TENNISCLIP_LOG_ENQUEUE", "0")

    repo = Path(args.repo).resolve()
    input_root = Path(args.input).resolve()
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    _boot_repos(repo)

    # 延迟导入 app.*（sys.path 就绪后）
    from app.config import AppConfig
    from app.models import TaskResult
    from app.utils.logger import get_logger

    logger = get_logger(__name__)

    # 内存版 AppConfig（Kaggle 无 config.yaml，用默认值 = config.yaml 缺省）
    config = AppConfig()
    config.llm.mock_mode = "auto"        # 无 API Key → LLM 节点 Mock 降级（验证链路正确性）
    config.llm.analysis_mode = "frame"
    config.paths.data_dir = str(out_root)  # 输出落到 /kaggle/working/
    # 把权重路径注入：cv_runtime.resolve_weights 优先用显式 weights 参数，
    # 节点侧 detect.tracknet 默认权重名 tracknet.pth，这里改 MODELS_DIR 指向 dataset
    import app.utils.cv_runtime as cv_runtime
    weights_dir = input_root / "weights"
    if weights_dir.is_dir():
        cv_runtime.MODELS_DIR = weights_dir
        logger.info("verify: MODELS_DIR → {}", weights_dir)

    graph = build_cv_debug_graph()

    # dry-run：仅验证 import 闭包 + 图编译 + PureExecutor 构建，不跑逐帧推理
    if args.dry_run:
        from app.models import TaskResult, TaskStatus
        _dry = TaskResult(task_id="dryrun", source_video="dryrun.mp4")
        _dry.status = TaskStatus.PROCESSING
        _dry_out = out_root / "dryrun"
        _dry_out.mkdir(parents=True, exist_ok=True)
        ex = PureExecutor(graph, config, _dry, level="intermediate", task_out=_dry_out)
        print(f"[verify] dry-run OK：图「{graph.name}」{len(graph._nodes_list)} 节点 / {len(graph.edges)} 边，"
              f"validate={graph.validate()['ok']}，PureExecutor 构建成功")
        return

    videos = [Path(args.video)] if args.video else find_videos(input_root)
    if not videos:
        raise SystemExit(f"[verify] {input_root}/data/ 下无 mp4 视频")

    weight_file = find_weights(input_root)
    logger.info("verify: 权重={} 设备={} 视频={} 张图", weight_file.name, args.device, len(videos))

    # 把 detect.tracknet 节点的 weights/device 参数设好（节点 params 缺省 {}）
    # detect.tracknet 的 spec.params 无 weights 键，resolve_params 只注入 spec.params 的键
    # → 改 MODELS_DIR（resolve_weights 的缺省路径）已足够让 tracknet.pth 被找到
    for node in graph.nodes.values():
        if node.type == "detect.tracknet":
            node.params = {**node.params, "device": args.device}
        elif node.type == "post.score_highlights":
            node.params = {**node.params, "device": args.device}
        elif node.type == "post.visualize_track":
            node.params = {**node.params, "device": args.device}

    all_results = []
    for i, vid in enumerate(videos):
        task_id = f"kaggle-{i:02d}"
        result = TaskResult(task_id=task_id, source_video=str(vid))
        from app.models import TaskStatus
        result.status = TaskStatus.PROCESSING
        task_out = out_root / task_id
        task_out.mkdir(parents=True, exist_ok=True)
        ex = PureExecutor(graph, config, result, level="intermediate", task_out=task_out)
        t0 = time.monotonic()
        ex.execute(vid)
        all_results.append({
            "video": vid.name,
            "status": str(result.status),
            "elapsed": round(time.monotonic() - t0, 2),
            "out_dir": str(task_out),
            "highlight": [s.model_dump() for s in (result.highlight.segments if result.highlight else [])],
            "artifacts": ex._artifact_records,
        })
        print(f"[verify] {vid.name} → {result.status} ({round(time.monotonic()-t0,1)}s) "
              f"产物={list(ex._artifact_records)}")

    summary = out_root / "summary.json"
    summary.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[verify] 完成 {len(all_results)} 条，汇总：{summary}")
    print(f"[verify] 下载 /kaggle/working/ 下各 kaggle-XX/track_overlay.mp4 + result.json 人工核对")


if __name__ == "__main__":
    main()
