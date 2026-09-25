#!/usr/bin/env python3
"""后台服务进程查询与清理（对应 AGENTS.md「边界与注意事项 · 禁止自动启动后台服务」）。

agent 不得自动启动常驻服务；但历史会话可能遗留已脱离管理的服务（父进程为 PID 1
的孤儿进程），本脚本用于**事后排查与回收**，避免端口占用、僵尸服务。

识别范围（仅本项目常驻服务，不含 nginx 等系统服务）：
    uvicorn（后端 API 8000）/ vite + esbuild（前端 dev 5173）/
    vitepress（文档站 dev）/ pnpm|npm|yarn dev（包装进程）/ sh -c 包装

安全约束：
    - 永不触碰自身进程链（自身 + 所有祖先）、IDE/code-server 进程、检索类进程
      （grep/ps/rg 等，避免误杀"正在搜 uvicorn 的 grep"）。
    - 默认要求进程 cwd 位于项目根内（`--any-cwd` 可放开），不误伤其他项目的同名服务。
    - `list` 只读不发信号；`clean` 先 TERM、校验、再 KILL、最后复验，绝不"发完就走"。

用法：
    python3 scripts/manage_services.py list              # 查询（默认子命令）
    python3 scripts/manage_services.py list --json       # 机器可读输出
    python3 scripts/manage_services.py clean --dry-run   # 演练：列出将清理的进程
    python3 scripts/manage_services.py clean --yes       # 非交互执行清理

退出码：0=无目标/已清理干净；1=清理后仍有残留；2=参数错误或交互中止。
仅支持 Linux（依赖 /proc），纯标准库无第三方依赖。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# (服务名, 命令行正则)。正则对完整 cmdline（NUL 已转空格）匹配，大小写敏感以避免误命中。
SERVICE_RULES: tuple[tuple[str, str], ...] = (
    ("uvicorn", r"\buvicorn\b"),
    ("vite", r"/vite/bin/vite\.js\b|node_modules/\.bin/vite\b"),
    ("vitepress", r"\bvitepress\b"),
    ("pnpm-npm-dev", r"(?:^|\s)(?:pnpm|npm|yarn)\s+(?:run\s+)?(?:-[^\s]+\s+)*dev\b"),
    ("esbuild", r"\besbuild\s+--service\b"),
    ("sh-wrapper", r"(?:^|\s)sh\s+-c\s+\S*(?:vite|uvicorn|pnpm dev)"),
)

# 永不清理：进程名前缀（argv[0] 基名）
IGNORED_ARGV0 = frozenset(
    {"grep", "egrep", "fgrep", "pgrep", "rg", "ps", "top", "awk", "sed", "head", "tail"}
)
# 永不清理：命令行子串（IDE / 检索工具 / 本脚本自身）
IGNORED_SUBSTRINGS = (
    "code-oss-dev",
    "bootstrap-fork",
    "extensionHost",
    "serverWorkerMain",
    "jsonServerMain",
    "manage_services.py",
)


@dataclass
class Proc:
    pid: int
    ppid: int
    argv0: str
    cmd: str
    cwd: str
    service: str = ""
    ports: list[int] = field(default_factory=list)
    start_ts: float = 0.0
    depth: int = 0

    @property
    def is_orphan(self) -> bool:
        """父进程为 1（或 0）说明已脱离原会话，典型的孤儿/失管进程。"""
        return self.ppid in (0, 1)


# --------------------------------------------------------------------------- /proc 读取


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _list_pids() -> list[int]:
    out: list[int] = []
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            out.append(int(entry))
    return sorted(out)


def _parse_stat(pid: int) -> tuple[int, float] | None:
    """返回 (ppid, starttime_ticks)。cmd 可能含空格与括号，须以最后一个 ')' 切分。"""
    raw = _read(f"/proc/{pid}/stat")
    if not raw:
        return None
    rparen = raw.rfind(")")
    if rparen < 0:
        return None
    fields = raw[rparen + 2 :].split()
    # 切分后 fields[0]=state，fields[1]=ppid，starttime 为整行第 22 字段
    try:
        return int(fields[1]), float(fields[19])
    except (IndexError, ValueError):
        return None


def _boot_time() -> float:
    for line in _read("/proc/stat").splitlines():
        if line.startswith("btime "):
            return float(line.split()[1])
    return 0.0


def _clk_tck() -> float:
    try:
        return float(os.sysconf("SC_CLK_TCK"))
    except (ValueError, OSError):
        return 100.0


def _readlink(path: str) -> str:
    try:
        return os.readlink(path)
    except OSError:
        return ""


def _match_service(cmd: str) -> str:
    for name, pattern in SERVICE_RULES:
        if re.search(pattern, cmd):
            return name
    return ""


def collect_procs() -> list[Proc]:
    """扫描 /proc，读取每个进程的 cmdline/cwd/ppid/启动时间（不做过滤）。"""
    boot, clk = _boot_time(), _clk_tck()
    procs: list[Proc] = []
    for pid in _list_pids():
        raw = _read(f"/proc/{pid}/cmdline")
        if not raw:
            continue
        argv = [p for p in raw.split("\0") if p]
        if not argv:
            continue
        stat = _parse_stat(pid)
        if stat is None:
            continue
        ppid, start_ticks = stat
        cmd = " ".join(argv)
        procs.append(
            Proc(
                pid=pid,
                ppid=ppid,
                argv0=Path(argv[0]).name,
                cmd=cmd,
                cwd=_readlink(f"/proc/{pid}/cwd"),
                start_ts=boot + start_ticks / clk,
            )
        )
    return procs


def _self_chain(procs: list[Proc]) -> set[int]:
    """自身 PID + 沿 ppid 上溯的全部祖先（这些进程绝不能被清理）。"""
    by_pid = {p.pid: p for p in procs}
    chain: set[int] = {os.getpid()}
    cur = os.getpid()
    for _ in range(64):  # 防御性上限，避免异常环路
        proc = by_pid.get(cur)
        if proc is None or proc.ppid in (0, 1) or proc.ppid in chain:
            break
        chain.add(proc.ppid)
        cur = proc.ppid
    return chain


# --------------------------------------------------------------------------- 进程树 / 端口


def _children_map(procs: list[Proc]) -> dict[int, list[Proc]]:
    m: dict[int, list[Proc]] = {}
    for p in procs:
        m.setdefault(p.ppid, []).append(p)
    return m


def expand_descendants(seeds: list[Proc], procs: list[Proc]) -> list[Proc]:
    """种子进程的全部后代一并纳入（如 uvicorn 的 multiprocessing 子进程、sh 包装层）。"""
    m = _children_map(procs)
    seen: dict[int, Proc] = {p.pid: p for p in seeds}
    stack = [p.pid for p in seeds]
    while stack:
        for child in m.get(stack.pop(), []):
            if child.pid not in seen:
                seen[child.pid] = child
                stack.append(child.pid)
    return list(seen.values())


def _assign_depth(targets: list[Proc]) -> None:
    by_pid = {p.pid: p for p in targets}
    for p in targets:
        depth, cur, guard = 0, p, 0
        while cur.ppid in by_pid and guard < 64:
            cur = by_pid[cur.ppid]
            depth += 1
            guard += 1
        p.depth = depth


def _listen_ports_by_inode() -> dict[str, int]:
    """inode -> 端口，扫描 /proc/net/{tcp,tcp6} 中 st=0A(LISTEN) 的行。"""
    out: dict[str, int] = {}
    for name in ("tcp", "tcp6"):
        raw = _read(f"/proc/net/{name}")
        for line in raw.splitlines()[1:]:
            f = line.split()
            if len(f) < 10 or f[3] != "0A":
                continue
            try:
                port = int(f[1].rsplit(":", 1)[1], 16)
                out[f[9]] = port  # f[9] = inode
            except (IndexError, ValueError):
                continue
    return out


def attach_ports(targets: list[Proc]) -> None:
    """给目标进程标注其持有的监听端口（端口在父进程上时父进程也须在目标集中）。"""
    inode_ports = _listen_ports_by_inode()
    if not inode_ports:
        return
    for p in targets:
        ports: set[int] = set()
        for fd in os.listdir(f"/proc/{p.pid}/fd"):
            link = _readlink(f"/proc/{p.pid}/fd/{fd}")
            if link.startswith("socket:["):
                port = inode_ports.get(link[8:-1])
                if port:
                    ports.add(port)
        p.ports = sorted(ports)


# --------------------------------------------------------------------------- 查找


def find_targets(
    root: Path = PROJECT_ROOT, any_cwd: bool = False
) -> tuple[list[Proc], list[Proc]]:
    """返回 (targets, skipped)。

    targets: 命中服务规则、且通过安全过滤（自身链/IDE/检索工具/cwd 归属）的进程，
             含其全部后代。
    skipped: 命中规则但被安全过滤排除的进程（打印出来供人工复核，避免"漏杀"无感知）。
    """
    procs = collect_procs()
    protected = _self_chain(procs)
    root_str = str(root.resolve())
    seeds: list[Proc] = []
    skipped: list[Proc] = []

    for p in procs:
        if p.pid in protected:
            continue
        service = _match_service(p.cmd)
        if not service:
            continue
        p.service = service
        if p.argv0 in IGNORED_ARGV0 or any(s in p.cmd for s in IGNORED_SUBSTRINGS):
            skipped.append(p)
            continue
        if not any_cwd:
            cwd = p.cwd
            if not cwd or not (cwd == root_str or cwd.startswith(root_str + os.sep)):
                skipped.append(p)
                continue
        seeds.append(p)

    targets = expand_descendants(seeds, procs)
    for p in targets:
        if not p.service:
            p.service = _match_service(p.cmd) or "子进程"
    _assign_depth(targets)
    attach_ports(targets)
    # 后代展开可能带回被保护的进程（理论上不该发生），再兜底剔除一次
    targets = [p for p in targets if p.pid not in protected]
    return sorted(targets, key=lambda p: p.pid), sorted(skipped, key=lambda p: p.pid)


# --------------------------------------------------------------------------- 展示


def _fmt_age(start_ts: float) -> str:
    if start_ts <= 0:
        return "?"
    sec = int(time.time() - start_ts)
    if sec < 0:
        sec = 0
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _short(cmd: str, width: int = 72) -> str:
    return cmd if len(cmd) <= width else cmd[: width - 1] + "…"


def print_table(targets: list[Proc], skipped: list[Proc]) -> None:
    if not targets:
        print(f"[ok] 未发现失管的后台服务进程（项目根 {PROJECT_ROOT}）")
    else:
        print(f"发现 {len(targets)} 个后台服务进程：")
        header = f"{'PID':>7} {'PPID':>7} {'运行时长':>9} {'端口':<11} {'服务':<14} {'孤儿':<4} 命令行"
        print(header)
        print("-" * len(header))
        for p in targets:
            ports = ",".join(str(x) for x in p.ports) or "-"
            print(
                f"{p.pid:>7} {p.ppid:>7} {_fmt_age(p.start_ts):>9} "
                f"{ports:<11} {p.service:<14} {'是' if p.is_orphan else '':<4} {_short(p.cmd)}"
            )
    if skipped:
        print(f"\n命中规则但已跳过 {len(skipped)} 个（安全过滤，人工复核用）：")
        for p in skipped:
            print(f"{p.pid:>7} cwd={p.cwd or '?'}  {_short(p.cmd)}")


def _alive(pid: int) -> bool:
    return Path(f"/proc/{pid}").exists()


# --------------------------------------------------------------------------- 清理


def terminate(targets: list[Proc], timeout: float, dry_run: bool) -> list[Proc]:
    """TERM → 等待校验 → KILL → 复验。返回清理后仍存活的进程列表。"""
    pids = [p.pid for p in targets]
    if dry_run:
        print(f"[dry-run] 将发送 SIGTERM 到 {len(pids)} 个进程：{pids}")
        return targets

    print(f"[1/3] SIGTERM → {len(pids)} 个进程")
    for p in sorted(targets, key=lambda x: -x.depth):  # 先子后父
        try:
            os.kill(p.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError as exc:
            print(f"  ! 无权限终止 {p.pid}: {exc}")

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(_alive(pid) for pid in pids):
            break
        time.sleep(0.2)

    survivors = [p for p in targets if _alive(p.pid)]
    if survivors:
        print(f"[2/3] {len(survivors)} 个未退出，SIGKILL → {[p.pid for p in survivors]}")
        for p in survivors:
            try:
                os.kill(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        time.sleep(min(2.0, timeout))
    else:
        print("[2/3] 全部已优雅退出，无需 KILL")

    time.sleep(0.3)  # 内核回收 /proc 条目稍有延迟
    remaining = [p for p in targets if _alive(p.pid)]
    print(f"[3/3] 复验：{'仍有 %d 个残留' % len(remaining) if remaining else '全部已退出'}")
    return remaining


def report_ports(remaining: list[Proc]) -> list[int]:
    """复验后仍被占用的端口（残留进程持有，或已被第三方重新绑定）。"""
    if remaining:
        attach_ports(remaining)
        return sorted({port for p in remaining for port in p.ports})
    # 目标已清空，直接看这些服务常用端口是否仍被占
    stuck: list[int] = []
    inode_ports = _listen_ports_by_inode()
    for p in collect_procs():
        if not inode_ports:
            break
        for fd_path in Path(f"/proc/{p.pid}/fd").glob("*"):
            link = _readlink(str(fd_path))
            if link.startswith("socket:["):
                port = inode_ports.get(link[8:-1])
                if port in (8000, 5173) and _match_service(p.cmd):
                    stuck.append(port)
    return sorted(set(stuck))


# --------------------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="manage_services.py",
        description="查询/清理本项目失管的后台服务进程（uvicorn / vite / vitepress 等）。",
    )
    sub = parser.add_subparsers(dest="command")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--any-cwd",
        action="store_true",
        help="不要求进程 cwd 在项目根内（默认要求，避免误伤其他项目）",
    )
    common.add_argument("--json", action="store_true", help="以 JSON 输出（仅 list）")

    sub.add_parser("list", parents=[common], help="查询后台服务进程（只读，默认）")
    clean = sub.add_parser("clean", parents=[common], help="清理后台服务进程")
    clean.add_argument("--yes", "-y", action="store_true", help="免交互直接执行")
    clean.add_argument(
        "--dry-run", action="store_true", help="仅列出将清理的进程，不发信号"
    )
    clean.add_argument(
        "--timeout", type=float, default=5.0, help="SIGTERM 后的等待秒数（默认 5）"
    )
    parser.add_argument(
        "command_pos",
        nargs="?",
        choices=("list", "clean"),
        default=None,
        help=argparse.SUPPRESS,  # 允许省略子命令（默认 list），保留 --help 可发现性
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if not Path("/proc").is_dir():
        print("本脚本仅支持 Linux（依赖 /proc）。", file=sys.stderr)
        return 2
    args = build_parser().parse_args(argv)
    command = args.command or args.command_pos or "list"
    any_cwd = getattr(args, "any_cwd", False)

    targets, skipped = find_targets(any_cwd=any_cwd)

    if command == "list":
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {
                        "count": len(targets),
                        "targets": [
                            {
                                "pid": p.pid,
                                "ppid": p.ppid,
                                "service": p.service,
                                "ports": p.ports,
                                "cwd": p.cwd,
                                "orphan": p.is_orphan,
                                "cmd": p.cmd,
                            }
                            for p in targets
                        ],
                        "skipped": [{"pid": p.pid, "cmd": p.cmd} for p in skipped],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print_table(targets, skipped)
        return 0 if not targets else 1

    # clean
    if not targets:
        print("[ok] 无待清理进程")
        return 0
    if getattr(args, "dry_run", False):
        terminate(targets, timeout=0, dry_run=True)
        print_table(targets, skipped)
        return 0
    if not getattr(args, "yes", False):
        if not sys.stdin.isatty():
            print(
                "非交互环境请显式加 --yes（或 --dry-run 预览），已中止。",
                file=sys.stderr,
            )
            return 2
        print_table(targets, skipped)
        answer = input(f"确认清理以上 {len(targets)} 个进程? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("已取消。")
            return 2

    remaining = terminate(targets, timeout=getattr(args, "timeout", 5.0), dry_run=False)
    ports = report_ports(remaining)
    if ports:
        print(f"! 端口仍被占用：{ports}")
    if skipped:
        print(f"提示：{len(skipped)} 个命中进程因安全过滤被跳过（见 list 输出）")
    return 1 if remaining else 0


if __name__ == "__main__":
    sys.exit(main())
