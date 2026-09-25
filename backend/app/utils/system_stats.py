"""运行时资源统计（CPU / 内存 / 任务队列 / GPU）。

仅依赖 stdlib，非 Linux 平台部分字段返回 None；GPU 数据需 nvidia-smi 可用。
供 /api/v1/system/stats 供前端资源面板轮询。

数据源优先级（容器环境更准确）：
- CPU 利用率 / 核数：cgroup v2（cpu.stat 差分 + cpu.max 配额）→ 回退 /proc/stat 差分
- 内存：cgroup v2（memory.current / memory.max）→ 回退 /proc/meminfo
- GPU：nvidia-smi（宿主设备，与容器无关）

注意：CPU% 必须基于**前后两次采样差分**计算——直接用累计 tick 比值得到的是
「开机以来平均值」，负载变化几乎反映不出来（历史缺陷）。
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

from app.utils.logger import get_logger

logger = get_logger(__name__)

# 采样间隔下限：过短的窗口会让差分值噪声过大
_MIN_SAMPLE_GAP = 0.05

# 模块级 CPU 采样基线：(monotonic 时间戳, kind, counters)
# kind ∈ {"cgroup", "proc"}，counters 结构见 _cpu_counters()
_PREV_CPU: tuple[float, str, dict[str, int]] | None = None


def _read(path: str | Path) -> str | None:
    try:
        return Path(path).read_text(errors="replace")
    except Exception:  # noqa: BLE001
        return None


def _cgroup_chain() -> list[Path]:
    """本进程 cgroup 到挂载根的目录列表，**近者优先**（用于逐级向上找限制值）。

    容器常见层级是「子组 cpu.max/max= unlimited，限制挂在父级（根）」，
    单读自身 cgroup 会拿到 "max" 而丢失真实配额，故需向上回溯。
    """
    rel = "/"
    raw = _read("/proc/self/cgroup")
    if raw:
        for line in raw.splitlines():
            if line.startswith("0::"):  # v2 单行格式：0::<path>
                rel = line[3:].strip() or "/"
                break
    root = Path("/sys/fs/cgroup")
    parts = [p for p in rel.split("/") if p and p != "."]
    built = [root]
    for p in parts:
        nxt = built[-1] / p
        if not nxt.exists():
            break
        built.append(nxt)
    return list(reversed(built))  # 自身 cgroup → 根


def _cgroup_cpu_quota() -> float | None:
    """cgroup CPU 配额（核数），沿祖先链向上找最近的数值限制。

    cpu.max 形如 "800000 100000" → 8.0；"max" 表示本级无限制，继续向上。
    """
    for d in _cgroup_chain():
        raw = _read(d / "cpu.max")
        if not raw:
            continue
        parts = raw.split()
        if len(parts) < 2 or parts[0] == "max":
            continue
        try:
            quota, period = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if quota > 0 and period > 0:
            return quota / period
    return None


def _cgroup_memory() -> tuple[int, int] | None:
    """cgroup 内存 (used, total) 字节。

    used 取自身 cgroup 的 memory.current（本容器实际占用）；
    total 沿祖先链向上找最近的数值 memory.max（子级为 "max" 时取父级限制）。
    """
    used: int | None = None
    total: int | None = None
    for d in _cgroup_chain():
        if used is None:
            raw = _read(d / "memory.current")
            if raw:
                try:
                    used = int(raw.strip())
                except ValueError:
                    pass
        if total is None:
            raw = _read(d / "memory.max")
            if raw and raw.strip() != "max":
                try:
                    val = int(raw.strip())
                    if val > 0:
                        total = val
                except ValueError:
                    pass
        if used is not None and total is not None:
            break
    if used is None or total is None:
        return None
    return used, total


def _parse_meminfo() -> dict[str, int]:
    raw = _read("/proc/meminfo")
    if not raw:
        return {}
    out: dict[str, int] = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            key = parts[0].rstrip(":")
            try:
                out[key] = int(parts[1]) * 1024  # kB → B
            except ValueError:
                pass
    return out


def _cpu_counters() -> tuple[str, dict[str, int]] | None:
    """采样 CPU 计数器：优先 cgroup usage_usec，回退 /proc/stat 累计 tick。

    Returns:
        ("cgroup", {"usage_usec": int}) 或 ("proc", {"idle": int, "total": int})；失败 None。
    """
    # cpu.stat 取自身 cgroup（仅本容器/作用域的累计用量，非宿主全局）
    for d in _cgroup_chain():
        raw = _read(d / "cpu.stat")
        if not raw:
            continue
        for line in raw.splitlines():
            if line.startswith("usage_usec "):
                try:
                    return "cgroup", {"usage_usec": int(line.split()[1])}
                except (ValueError, IndexError):
                    break

    raw = _read("/proc/stat")
    if raw:
        for line in raw.splitlines():
            if line.startswith("cpu "):
                parts = line.split()
                if len(parts) >= 5:
                    try:
                        vals = [int(v) for v in parts[1:]]
                    except ValueError:
                        break
                    # total = 全部字段和；idle = idle + iowait
                    total = sum(vals)
                    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
                    return "proc", {"idle": idle, "total": total}
    return None


def _diff_cpu_percent(
    prev: tuple[float, str, dict[str, int]],
    cur: tuple[str, dict[str, int]],
    quota_cores: float | None,
    now: float | None = None,
) -> float | None:
    """基于两次采样差分计算 CPU 利用率（0~100）。窗口过短/计数回退返回 None。

    Args:
        now: 当前 monotonic 时间戳，None 则实时取（测试注入用）。
    """
    prev_ts, prev_kind, prev_cnt = prev
    cur_kind, cur_cnt = cur
    if cur_kind != prev_kind:
        return None
    dt = (now if now is not None else time.monotonic()) - prev_ts
    if dt < _MIN_SAMPLE_GAP:
        return None

    pct: float | None = None
    if cur_kind == "cgroup":
        d_usage = cur_cnt.get("usage_usec", 0) - prev_cnt.get("usage_usec", 0)
        cores = quota_cores or float(os.cpu_count() or 1)
        if d_usage >= 0 and cores > 0:
            # usage_usec 是累计 CPU 时间，除以墙钟时间与配额核数得相对占用率
            pct = (d_usage / 1_000_000.0) / dt / cores * 100.0
    else:
        d_total = cur_cnt.get("total", 0) - prev_cnt.get("total", 0)
        d_idle = cur_cnt.get("idle", 0) - prev_cnt.get("idle", 0)
        if d_total > 0:
            pct = 100.0 * (1.0 - d_idle / d_total)

    if pct is None:
        return None
    return round(max(0.0, min(100.0, pct)), 1)


def _nvidia_gpu() -> list[dict[str, Any]]:
    try:
        out = subprocess.check_output(
            ["nvidia-smi",
             "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            text=True,
            timeout=5,
        )
        result = []
        for line in out.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 5:
                continue
            try:
                mem_used = int(parts[2])
                mem_total = int(parts[3])
            except ValueError:
                continue
            result.append({
                "index": int(parts[0]),
                "name": parts[1],
                # 字段名保留 _mb（历史契约），实际单位为字节，前端 fmtBytes 按字节格式化
                "memory_used_mb": mem_used * 1024 * 1024,
                "memory_total_mb": mem_total * 1024 * 1024,
                "utilization_percent": int(parts[4]) if parts[4] else 0,
            })
        return result
    except Exception:  # noqa: BLE001
        return []


def get_system_stats(
    running_tasks: int = 0,
    queued_tasks: int = 0,
) -> dict[str, Any]:
    """聚合 CPU/内存/任务/GPU 的统计对象，字段均为 JSON 可序列化类型。

    Args:
        running_tasks: 当前正在执行的任务数（PROCESSING 状态）。
        queued_tasks: 在队列中等待的任务数（PENDING 状态）。

    Returns:
        {
            cpu: {percent, cores},        # percent 首次采样为 None，之后为差分实时值
            memory: {total, used, available, percent},
            tasks: {running, queued},
            gpu: [...] 或 None,
        }
    """
    global _PREV_CPU  # noqa: PLW0603  模块级采样基线（跨请求差分）

    quota_cores = _cgroup_cpu_quota()

    # CPU：与上一次采样差分（首次调用建立基线，percent 为 None）
    sample = _cpu_counters()
    cpu_pct: float | None = None
    if sample is not None:
        if _PREV_CPU is not None:
            cpu_pct = _diff_cpu_percent(_PREV_CPU, sample, quota_cores)
        _PREV_CPU = (time.monotonic(), sample[0], sample[1])
    cores = int(quota_cores) if quota_cores else (os.cpu_count() or 1)

    # 内存：cgroup 优先（容器真实配额），回退 meminfo
    mem_total = mem_used = mem_avail = 0
    mem_pct: float | None = None
    cg_mem = _cgroup_memory()
    if cg_mem:
        mem_used, mem_total = cg_mem
        mem_avail = max(0, mem_total - mem_used)
        mem_pct = round(100.0 * mem_used / mem_total, 1)
    else:
        meminfo = _parse_meminfo()
        mem_total = meminfo.get("MemTotal", 0)
        mem_avail = meminfo.get("MemAvailable", meminfo.get("MemFree", 0))
        mem_used = mem_total - mem_avail if mem_total else 0
        mem_pct = round(100.0 * mem_used / mem_total, 1) if mem_total else None

    return {
        "cpu": {
            "percent": cpu_pct,
            "cores": cores,
        },
        "memory": {
            "total": mem_total,
            "used": mem_used,
            "available": mem_avail,
            "percent": mem_pct,
        },
        "tasks": {
            "running": running_tasks,
            "queued": queued_tasks,
        },
        "gpu": _nvidia_gpu() or None,
    }
