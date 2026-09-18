"""任务队列：批量处理限流稳载（对应风险2 规避方案）。"""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from app.config import AppConfig
from app.models import TaskResult, TaskStatus
from app.utils.logger import get_logger

logger = get_logger(__name__)


class TaskQueue:
    """线程池 + 状态表的轻量任务队列。

    - max_concurrent_tasks 限流，避免批量堆积；
    - 单任务超时保护（task_timeout_seconds）。
    """

    def __init__(self, config: AppConfig):
        self._config = config
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, config.queue.max_concurrent_tasks)
        )
        self._tasks: dict[str, TaskResult] = {}
        self._task_order: list[str] = []

    def submit(self, job: Callable[["TaskResult"], TaskResult], task_id: Optional[str] = None) -> str:
        if task_id is None:
            task_id = uuid.uuid4().hex[:12]
        self._tasks[task_id] = TaskResult(task_id=task_id, status=TaskStatus.PENDING)

        def _wrapped() -> None:
            result = self._tasks[task_id]
            result.status = TaskStatus.PROCESSING
            start = time.monotonic()
            try:
                # 把队列中同一 result 对象传给 job，job 原地修改（如 run_pipeline 设 stage），
                # 使轮询 get_task 能实时看到 stage/status 变化。
                out = job(result)
                elapsed = time.monotonic() - start
                # 用 job 返回的真实结果（含 failed/error）替换存储对象，
                # 不再无条件覆盖为 succeeded，保证异常原因能传回前端。
                if isinstance(out, TaskResult):
                    out.task_id = task_id
                    out.elapsed_seconds = elapsed
                    self._tasks[task_id] = out
                else:
                    result.status = TaskStatus.SUCCEEDED
                    result.elapsed_seconds = elapsed
            except TimeoutError:
                result.status = TaskStatus.TIMEOUT
                result.error = "task timeout"
                result.elapsed_seconds = time.monotonic() - start
            except Exception as exc:  # noqa: BLE001
                result.status = TaskStatus.FAILED
                result.error = str(exc)
                result.elapsed_seconds = time.monotonic() - start
                logger.exception("task {} failed", task_id)

        fut = self._executor.submit(_wrapped)
        fut.add_done_callback(lambda f: None)
        self._task_order.append(task_id)
        return task_id

    def get(self, task_id: str) -> TaskResult:
        if task_id not in self._tasks:
            raise KeyError(task_id)
        return self._tasks[task_id]

    def get_task_statuses(self) -> list[TaskResult]:
        return [self._tasks[tid] for tid in self._task_order if tid in self._tasks]

    def task_ids(self) -> list[str]:
        return list(self._task_order)

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)
