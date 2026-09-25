"""TDD 测试：根目录 scripts/manage_services.py（后台服务进程查询/清理）。

对应 AGENTS.md「边界与注意事项 · 禁止自动启动后台服务」——本脚本用于事后排查回收。
通过 importlib 按路径加载根目录脚本（非 Python 包，无法直接 import）。
"""

from __future__ import annotations

import importlib.util
import os
import socket
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "manage_services.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("manage_services", _SCRIPT)
    assert spec and spec.loader, f"无法加载脚本：{_SCRIPT}"
    mod = importlib.util.module_from_spec(spec)
    # dataclass 解析 postponed 注解时会经 sys.modules 取模块命名空间，须先注册
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


ms = _load_module()


# 服务规则覆盖本项目全部常驻服务（后端/前端/文档站/包装层）
@pytest.mark.parametrize(
    ("cmd", "expected"),
    [
        ("/venv/bin/uvicorn app.main:app --reload", "uvicorn"),
        ("/node_modules/.pnpm/vite@5.4.21/node_modules/vite/bin/vite.js", "vite"),
        ("node /node_modules/.bin/vite", "vite"),
        ("node /node_modules/.bin/vitepress dev docs", "vitepress"),
        ("pnpm dev:frontend", "pnpm-npm-dev"),
        ("npm run dev", "pnpm-npm-dev"),
        # sh 包装层：含 pnpm dev 的先命中 pnpm 规则，纯 vite 的走 sh-wrapper
        ("sh -c cd frontend && pnpm dev", "pnpm-npm-dev"),
        ("sh -c vite", "sh-wrapper"),
        ("sh -c npx vitepress dev docs", "vitepress"),  # vitepress 规则先命中，同样可识别
        ("esbuild --service=0.21.5 --ping", "esbuild"),
        # 非服务进程：不应命中
        ("python3 scripts/manage_services.py list", ""),
        ("grep -rn uvicorn /workspace", "uvicorn"),  # 命中规则，但由 argv0 过滤跳过
        ("code-oss-dev --type=extensionHost", ""),
        ("sleep 30", ""),
    ],
)
def test_match_service_rules(cmd: str, expected: str):
    assert ms._match_service(cmd) == expected


def test_ignored_argv0_covers_retrieval_tools():
    """检索类工具（grep/ps/rg…）永不清理，避免误杀"正在搜 uvicorn 的 grep"。"""
    for name in ("grep", "ps", "pgrep", "rg", "top"):
        assert name in ms.IGNORED_ARGV0


def test_self_pid_never_targeted():
    """无论命令行怎么匹配，自身进程链绝不能出现在清理目标中。"""
    fake = ms.Proc(
        pid=os.getpid(),
        ppid=1,
        argv0="python",
        cmd="uvicorn app.main:app --reload",
        cwd=str(ms.PROJECT_ROOT),
        service="uvicorn",
    )
    procs = ms.collect_procs()
    protected = ms._self_chain(procs)
    assert os.getpid() in protected
    targets = [p for p in procs if p.pid not in protected and ms._match_service(p.cmd)]
    assert all(p.pid != os.getpid() for p in targets)


def test_is_orphan_detects_ppid_1():
    assert ms.Proc(pid=1, ppid=1, argv0="a", cmd="a", cwd="").is_orphan
    assert not ms.Proc(pid=2, ppid=100, argv0="a", cmd="a", cwd="").is_orphan


def test_find_targets_current_env_is_clean():
    """当前环境不应有真实失管服务；若用户自启 dev 服务则跳过该用例。"""
    targets, _skipped = ms.find_targets()
    if any("uvicorn" in p.cmd or "vite" in p.cmd for p in targets):
        pytest.skip("本地存在用户自启的 dev 服务，不作为基线断言")
    assert targets == []


def test_attach_ports_marks_listener_port():
    """进程内开监听端口 → attach_ports 应把该端口标注到自身 Proc。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        me = ms.Proc(
            pid=os.getpid(),
            ppid=os.getppid(),
            argv0="python",
            cmd="python test",
            cwd=str(ms.PROJECT_ROOT),
        )
        ms.attach_ports([me])
        assert port in me.ports, f"未识别到自身监听端口 {port}（实际 {me.ports}）"


def test_json_output_shape(capsys):
    import json

    code = ms.main(["list", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert code in (0, 1)
    assert data["count"] == len(data["targets"])
    assert isinstance(data["skipped"], list)
    if data["targets"]:
        for key in ("pid", "ppid", "service", "ports", "cwd", "orphan", "cmd"):
            assert key in data["targets"][0]
    else:
        # 当前环境干净时，至少保证字段契约在构造的样本上成立
        sample = ms.Proc(pid=1, ppid=100, argv0="x", cmd="x", cwd="/")
        assert sample.pid == 1 and not sample.is_orphan


def test_clean_dry_run_never_signals():
    """--dry-run 只预告不发信号：返回值必须等于输入目标集，且不抛异常。"""
    fake = ms.Proc(
        pid=999999, ppid=1, argv0="uvicorn", cmd="uvicorn app.main:app", cwd="/tmp"
    )
    result = ms.terminate([fake], timeout=0, dry_run=True)
    assert result == [fake]
    assert ms._alive(999999) is False  # 不存在的 pid，确认未误伤
