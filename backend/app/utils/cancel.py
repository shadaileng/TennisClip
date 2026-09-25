"""任务协作式取消：模块级取消旗标 + TaskCancelled 异常 + 检查点辅助。

设计（最小侵入，方案 12 · 用户停止需求）：
- 协作式而非抢占式：Python 线程无法安全抢占，故在关键检查点（节点边界 / 逐帧
  推理循环 / ffmpeg 调用前后）主动检测旗标并抛 TaskCancelled，executor 捕获后
  走正常 failed 分支（record_task_finish 落库 failed，重启恢复钩子不会复活）。
- 长调用（ffmpeg 子进程 / LLM HTTP / torch 逐帧）无法从外部硬中断，取消语义为
  「在当前检查点之后立即停止、不再进入下一节点/下一帧/下一轮」——ffmpeg 跑完
  当前转码即在节点边界停止；LLM 请求返回后立即停止；torch 推理在下一采样帧前停止。
- 内存态（进程内 set）：服务重启自动清空，已落库 failed 的任务不受影响。
"""

from __future__ import annotations

import threading

from app.utils.logger import get_logger

logger = get_logger(__name__)


class TaskCancelled(Exception):
    """任务被用户显式停止（区别于一般失败）。executor 捕获后走 failed 分支。"""

    def __init__(self, task_id: str = ""):
        self.task_id = task_id
        super().__init__(f"任务 {task_id} 已被用户停止" if task_id else "任务已被用户停止")


_cancelled: set[str] = set()
_lock = threading.Lock()


def cancel_task(task_id: str) -> bool:
    """标记任务取消（幂等）。返回是否实际标记了新任务（False=已取消过）。"""
    with _lock:
        if task_id in _cancelled:
            return False
        _cancelled.add(task_id)
    logger.info("cancel: 任务 {} 已标记取消", task_id)
    return True


def is_cancelled(task_id: str) -> bool:
    """任务是否已被标记取消。"""
    with _lock:
        return task_id in _cancelled


def clear_cancelled(task_id: str) -> None:
    """清除取消旗标（任务终态后调用，避免 set 无限增长）。"""
    with _lock:
        _cancelled.discard(task_id)


def check_cancelled(task_id: str, label: str = "") -> None:
    """取消检查点：若任务已取消则抛出 TaskCancelled。

    各长循环（逐帧推理）/ 节点边界（executor 每层前）/ ffmpeg 调用（run 前后）
    统一调用此函数，保证「停止后最迟一个检查点内生效」。
    """
    if is_cancelled(task_id):
        logger.info("cancel: 任务 {} 在 [{}] 命中取消", task_id, label or "checkpoint")
        raise TaskCancelled(task_id)
