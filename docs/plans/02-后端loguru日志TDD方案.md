# 后端 loguru 日志系统接入（TDD 方案）

> **本页信息**
>
> | 项目 | 内容 |
> |------|------|
> | 文档编号 | 02 |
> | 文档版本 | v1.0.3 |
> | 文档状态 | 🏁 已完成 |
> | 最后更新 | 2026-09-25 |
> | 对应功能/内容 | 后端由标准 logging 替换为 loguru：双 sink（控制台 + 滚动文件）、统一拦截 uvicorn/FastAPI 日志、config.yaml + 环境变量控制级别、统一格式与带参调用规范 |
>
> **变更历史**
>
> | 日期 | 版本 | 说明 |
> |------|:----:|------|
> | 2026-09-25 | v1.0.3 | 日志时间加 UTC 标志：`{time:YYYY-MM-DD HH:mm:ss.SSSZ!UTC}` 强制换算 UTC 并输出 `+00:00` 偏移，与服务器时区解耦、便于本地化换算；新增 TC-07 |
> | 2026-09-16 | v1.0.2 | 修复 TC-04 跨测试污染：InterceptHandler 接管时复位被接管 logger 的 `disabled`/`level` 与全局 `logging.disable`；测试夹具 `_reset_logging` 新增 `_reset_stdlib` 重置标准库全局状态，避免第三方导入（uvicorn 等）静默禁用导致拦截失效 |
> | 2026-09-15 | v1.0.1 | 实施完成：TC-01~TC-06 全绿，27 处 %s 日志调用迁移为 {} 占位符，接入 uvicorn 拦截 |
> | 2026-09-15 | v1.0.0 | 初版（TDD 模式方案） |
>
> **关联文档**：[01-需求分析与落地方案](./01-需求分析与落地方案.md)、[AGENTS.md](../AGENTS.md)

## 一、背景与目标

当前 `backend/app/utils/logger.py` 基于 Python 标准 `logging`，仅输出到 stderr，且未利用 `config.yaml` 的 `logging.level`（只读环境变量）。目标：引入 `loguru`，在不改动现有 12 处 `get_logger(name)` 调用点的前提下，提供统一、结构化、可落盘的日志体系。

**目标**
- 统一格式规范（控制台彩色 / 文件纯文本），风格一致、便于 grep 与归档。
- 双输出：控制台（stderr）+ 滚动文件 `backend/data/app.log`（`rotation=10 MB`、`retention=7 days`、`compression="zip"`）。
- 通过 `InterceptHandler` 统一接管 uvicorn/FastAPI 标准 `logging`，全链路风格一致。
- 级别来源：环境变量 `TENNISCLIP_LOG_LEVEL` > `config.yaml` 的 `logging.level` > 默认 `INFO`。
- 带参调用规范：延迟求值 `{}` 占位符、`bind()` 结构化上下文。

**适用范围**：`backend/` 全部模块及 uvicorn 运行期日志；前端不涉及。

## 二、TDD 总体策略（Red → Green → Refactor）

本方案采用测试驱动开发：先编写**失败**的测试用例（Red），再实现 `logger.py` 使其通过（Green），最后在不破坏测试的前提下做清理与文档同步（Refactor）。

- **测试文件**：新建 `backend/tests/test_logging.py`（pytest，与现有 `test_pipeline.py` 同框架）。
- **被测对象**：`app.utils.logger` 的 `get_logger`、`setup_logging`、以及 `InterceptHandler`。
- **可测性设计**：`setup_logging(log_file: Path | None = None)` 提供可选 `log_file` 入参（默认 `config.path("data") / "app.log"`），测试时可指向 `tmp_path` 避免污染与跨用例干扰；因 loguru 为进程级单例，测试需在每个用例内重建 handler（见第三节 `conftest` 思路）。
- **级别解析**：实现从 `os.environ.get("TENNISCLIP_LOG_LEVEL")` → `load_config().logging_level` → `"INFO"`，测试通过 monkeypatch 环境变量与隔离 `config` 验证。

## 三、测试用例设计（先写，预期 Red）

> 下列用例在「未实现 loguru」时全部失败；实现后应为 Green。用例与统一格式规范、级别来源、拦截机制一一对应。

### TC-01 返回 loguru 风格 logger 且绑定模块名
- 调用 `get_logger("my_module")` 返回对象具备 `.info/.error/.bind` 等方法（非标准 `logging.Logger`）。
- 通过该 logger 写入日志后，日志行中能定位到 `my_module`（经 `extra[name]` 注入）。

### TC-02 日志写入文件 sink
- 以 `tmp_path/"app.log"` 初始化 `setup_logging(log_file=tmp_path/"app.log")`。
- `get_logger("svc").info("hello {}", "world")` 后，断言日志文件内容包含 `hello world` 且匹配统一格式（`时间 | 级别 | 模块:函数:行号 - 消息`）。
- 断言文件为 UTF-8、纯文本（无 ANSI 转义色块）。

### TC-03 级别来源：环境变量覆盖 config.yaml
- monkeypatch `TENNISCLIP_LOG_LEVEL="DEBUG"`，其余默认，断言生效级别为 DEBUG。
- 清除该环境变量、`config.yaml` 设 `logging.level: WARNING`，断言生效级别为 WARNING。
- 两者皆无，断言兜底为 INFO。

### TC-04 标准 logging 被统一拦截（uvicorn 场景）
- 初始化后，调用标准库 `logging.getLogger("uvicorn.access").info("GET /health")`。
- 断言该消息出现在 loguru 的 sink（文件/捕获流）中，且沿用统一格式（证明经 `InterceptHandler` 汇入）。

### TC-05 滚动文件参数生效
- 初始化时 `rotation="10 MB"`、`retention="7 days"`、`compression="zip"` 被传入文件 sink。
- 通过读取 `logger._core.handlers` 的 sink 配置断言上述参数存在（或等价地，构造 >10MB 写入后断言产生 `.zip` 归档）。

### TC-06 带参调用规范（延迟求值 + bind）
- `logger.info("path={} task_id={}", p, t)` 正确展开为 `path=<p> task_id=<t>`（验证 `{}` 位置参数 `str.format` 语义）。
- `logger.bind(task_id=t).error("fail: {}", exc)` 日志中保留 `task_id` 上下文（验证 `bind().extra` 机制）。

### TC-07 时间为 UTC 并带 +00:00 偏移标志（便于本地化换算）
- 日志首行时间形如 `2026-09-25 02:52:47.004+00:00 | INFO ...`，正则断言 `\+00:00` 偏移存在。
- 将捕获的时间按 UTC 解析，与 `datetime.now(timezone.utc)` 相差 < 5 秒（证明经 `!UTC` 强制换算、非本地时间直接贴标）。

### 测试脚手架要点（`backend/tests/test_logging.py`）
```python
import logging
from pathlib import Path
import pytest
from app.utils import logger as logger_mod
from app.utils.logger import InterceptHandler, get_logger, setup_logging, teardown_logging

_STDLIB_LOGGERS = ("uvicorn", "uvicorn.access", "uvicorn.error", "fastapi")


@pytest.fixture(autouse=True)
def _reset_logging():
    """每个用例前后重置 loguru 单例与标准库 logging 全局状态，避免跨测试污染。

    uvicorn 等第三方在导入期可能把 logger.disabled 置 True 或调用
    logging.disable(...)，导致 isEnabledFor 短路、InterceptHandler 收不到日志。
    """

    def _reset_stdlib() -> None:
        logging.disable(0)  # 复位全局禁用级别
        for name in _STDLIB_LOGGERS:
            lg = logging.getLogger(name)
            lg.setLevel(logging.NOTSET)
            lg.handlers = []
            lg.propagate = True
            lg.disabled = False  # 复位第三方可能设置的禁用标记
        root = logging.getLogger()
        root.handlers = [
            h for h in root.handlers if not isinstance(h, InterceptHandler)
        ]

    _reset_stdlib()
    teardown_logging()  # 移除已注册 sink + 复位 _configured
    yield
    teardown_logging()
    _reset_stdlib()


@pytest.fixture
def tmp_log_file(tmp_path):
    log_file = tmp_path / "app.log"
    # 重建 loguru 单例 handler，指向临时文件，避免污染 data/app.log
    setup_logging(log_file=log_file)
    yield log_file
    # teardown 由 autouse _reset_logging 统一处理
```

## 四、实现步骤（Green：使上述测试通过）

- [ ] **Step 1 依赖**：`pyproject.toml` 的 `dependencies` 增加 `"loguru>=0.7"`，执行 `uv sync` 更新 `uv.lock`。
- [ ] **Step 2 重写 `logger.py`**：
  - 定义统一格式串：`FMT_TEXT = "{time:YYYY-MM-DD HH:mm:ss.SSSZ!UTC} | {level: <8} | {extra[name]}:{function}:{line} - {message}"`（时间经 `!UTC` 强制换算、`Z` 输出 `+00:00` 偏移）；控制台彩色版加 `<green>/<level>/<cyan>` 标签。
  - `setup_logging(log_file=None)`：幂等（`_configured` 标志）；添加控制台 sink（`sys.stderr`, `colorize=True`）与文件 sink（`log_file`, `rotation="10 MB"`, `retention="7 days"`, `compression="zip"`, `encoding="utf-8"`）；挂载 `InterceptHandler` 到 `logging.root` 并接管 uvicorn/fastapi logger。
  - `get_logger(name, level=None)`：返回 `logger.bind(name=name)`（保持 12 处调用点兼容）。
  - 级别解析：按 TC-03 顺序取值。
  - `teardown_logging()`：测试清理用，移除已注册 sink 并复位 `_configured`。
- [ ] **Step 3 入口接入**：`main.py`、`cli.py` 在导入期/入口显式调用 `setup_logging()`（幂等，无副作用），确保 uvicorn 启动期日志也被接管。
- [ ] **Step 4 忽略日志文件**：`.gitignore` 补充 `backend/data/*.log`。
- [ ] **Step 5 文档**：更新 `AGENTS.md` 日志约定（统一格式、带参调用格式、级别来源、日志路径、JSON 备选）。
- [ ] **Step 6 运行测试**：`uv run pytest backend/tests/test_logging.py` 全绿。

## 五、验收标准

- `backend/tests/test_logging.py` 中 TC-01~TC-07 全部通过。
- 启动 `uv run uvicorn app.main:app --port 8000` 时，控制台与 `backend/data/app.log` 均按统一格式输出，且 uvicorn 启动/访问日志同格式呈现。
- 设置 `TENNISCLIP_LOG_LEVEL=DEBUG` 可覆盖 `config.yaml` 级别。
- 现有 12 处 `get_logger(name)` 调用点无改动且工作正常。
- `backend/data/app.log` 滚动归档正常，且被 `.gitignore` 忽略、不入库。

## 六、风险与应对

| 风险 | 影响 | 应对方案 |
|------|------|---------|
| loguru 进程级单例导致测试间 handler 累积 | 测试污染、重复日志 | `setup_logging` 幂等 + `teardown_logging` 清理；测试用 `tmp_path` |
| `InterceptHandler` 栈深度（`depth`）设置不当 | 日志显示 loguru 内部帧 | `emit` 用 `logger.opt(depth=6, exception=record.exc_info).log(...)` |
| 文件 sink 在容器/无写入权限时失败 | 服务启动报错 | `setup_logging` 对文件 sink 失败做降级（仅控制台），不影响主流程 |
| 第三方库（uvicorn 等）在导入期把被接管 logger 的 `disabled` 置 `True` 或调用 `logging.disable(...)` | 标准 logging 被静默禁用，`isEnabledFor` 短路，`InterceptHandler` 收不到日志（TC-04 在完整套件下偶发失败） | `_intercept_stdlib_logging` 接管时复位 `logging.disable(0)` 及每个被接管 logger 的 `level=NOTSET`/`disabled=False`；测试夹具 `_reset_stdlib` 在每个用例前后重置标准库全局状态 |
| 滚动/压缩参数拼写错误被静默忽略 | 日志无限增长 | TC-05 显式断言 sink 参数，CI 校验 |

## 七、关联文档

- [01-需求分析与落地方案](./01-需求分析与落地方案.md)
- [AGENTS.md](../AGENTS.md)（日志约定将在 Step 5 同步更新）
- loguru 官方文档：<https://loguru.readthedocs.io/en/stable/api/logger.html>（格式令牌、`bind()`、`opt(lazy=True)` 依据）
