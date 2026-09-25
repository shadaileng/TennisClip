"""运行时资源统计（CPU / 内存 / 任务队列 / GPU）。

仅依赖 stdlib，非 Linux 平台部分字段返回 None；GPU 数据需 nvidia-smi 可用。
供 /api/v1/system/stats 供前端资源面板轮询。
"""

from __future__ import annotations

import os
import resource
import subprocess
from pathlib import Path
from typing import Any

from app.utils.logger import get_logger

logger = get_logger(__name__)


def _read_proc(path: str) -> str | None:
    try:
        return Path(path).read_text(errors="replace")
    except Exception:  # noqa: BLE001
        return None


def _parse_meminfo() -> dict[str, int]:
    raw = _read_proc("/proc/meminfo")
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


def _parse_cpu_stat() -> dict[str, int]:
    raw = _read_proc("/proc/stat")
    if not raw:
        return {}
    for line in raw.splitlines():
        if line.startswith("cpu "):
            parts = line.split()
            if len(parts) >= 8:
                try:
                    return {
                        "user": int(parts[1]),
                        "nice": int(parts[2]),
                        "system": int(parts[3]),
                        "idle": int(parts[4]),
                        "iowait": int(parts[5]),
                        "irq": int(parts[6]),
                        "softirq": int(parts[7]),
                    }
                except ValueError:
                    break
    return {}


def _get_pid_cpu_percent(pid: int, prev: dict[str, int] | None) -> tuple[float, dict[str, int]] | tuple[None, None]:
    """基于 /proc/stat 的差值计算 CPU 百分比（单样本近似，精确需两次采样）。"""
    try:
        stat_line = _read_proc(f"/proc/{pid}/stat")
        if not stat_line:
            return None, None
        # fields 4=utime, 5=stime, 22=starttime (unit=clk_t)
        parts = stat_line.split(") ")
        if len(parts) < 2:
            return None, None
        fields = parts[1].split()
        if len(fields) < 22:
            return None, None
        cur = {
            "user": int(fields[11]),  # utime
            "system": int(fields[12]),  # stime
            "idle": 0,
            "iowait": int(fields[13]) if len(fields) > 13 else 0,
        }
        # 单次采样：(user+system) / (clk_t per sec) 作为瞬时占比近似
        # 更精确需要前后两次采样差值，这里返回累计 tick 供调用方自行计算
        total = cur["user"] + cur["system"] + cur["iowait"]
        if prev:
            dt = (total - sum(prev.values()))
            # 系统时钟频率
            clk = os.sysconf("SC_CLK_TCK") if "SC_CLK_TCK" in os.sysconf_names else 100
            dt_sec = dt / clk
            # 简化：取 (user+system) 增量占 (user+system+iowait) 增量的比例作为当前任务 CPU 占比
            p_total = sum(prev.values())
            if p_total == 0 or dt_sec <= 0:
                return None, cur
            active_prev = prev.get("user", 0) + prev.get("system", 0)
            active_cur = cur["user"] + cur["system"]
            pct = round((active_cur - active_prev) / (dt * clk / 100) * 100, 1) if clk and dt else None
            if pct is not None:
                pct = max(0.0, min(100.0, pct))
            return pct, cur
        return None, cur
    except Exception:  # noqa: BLE001
        return None, None


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
    pid: int | None = None,
    prev_cpu: dict[str, int] | None = None,
) -> dict[str, Any]:
    """聚合 CPU/内存/任务/GPU 的统计对象，字段均为 JSON 可序列化类型。

    Args:
        running_tasks: 当前正在执行的任务数（PROCESSING 状态）。
        queued_tasks: 在队列中等待的任务数（PENDING 状态）。
        pid: 当前进程 PID（None 则用 os.getpid()）。
        prev_cpu: 上一次调用的 _get_pid_cpu_percent 返回值（用于累进计算）。

    Returns:
        {
            cpu: {percent, cores},
            memory: {total, used, available, percent},
            tasks: {running, queued},
            gpu: [...] 或 None,
        }
    """
    if pid is None:
        pid = os.getpid()

    # CPU
    pstat = _parse_cpu_stat()
    online_cpus = os.cpu_count() or 1
    # 近似利用率（基于系统整体累计 tick）
    cpu_pct = None
    if pstat:
        total_tick = sum(pstat.values())
        idle_tick = pstat.get("idle", 0) + pstat.get("iowait", 0)
        if total_tick > 0:
            cpu_pct = round(100.0 * (1.0 - idle_tick / total_tick), 1)

    # 内存
    meminfo = _parse_meminfo()
    mem_total = meminfo.get("MemTotal", 0)
    mem_avail = meminfo.get("MemAvailable", meminfo.get("MemFree", 0))
    mem_used = mem_total - mem_avail if mem_total else 0
    mem_pct = round(100.0 * mem_used / mem_total, 1) if mem_total else None

    # GPU
    gpus = _nvidia_gpu() or None

    # 进程 CPU（可选，保留历史做差分）
    proc_cpu_pct: float | None = None
    new_prev: dict[str, int] | None = None
    if pid > 0:
        proc_cpu_pct, new_prev = _get_pid_cpu_percent(pid, prev_cpu)

    return {
        "cpu": {
            "percent": cpu_pct,
            "cores": online_cpus,
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
        "gpu": gpus,
    }
