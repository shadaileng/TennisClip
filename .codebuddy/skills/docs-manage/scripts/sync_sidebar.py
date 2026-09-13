#!/usr/bin/env python3
"""docs-manage skill 辅助脚本：校验方案文档是否已同步到 VitePress 侧边栏。

用法：
    python sync_sidebar.py                  # 校验 docs/plans 下所有文档是否已进侧边栏，输出遗漏清单
    python sync_sidebar.py --append         # 将遗漏文档自动追加到侧边栏默认分组（"测试与工程优化"）

说明：
- 扫描 docs/plans/*.md，提取文件名；侧边栏配置为 docs/.vitepress/config.mts。
- 侧边栏中每一条目的 link 形如 '/plans/73-测试体系引入-env-test实现环境隔离'，
  即文件名去掉 .md 后缀并加 '/plans/' 前缀。
- --append 模式下，遗漏文档按文件名字典序追加到 "测试与工程优化" 分组末尾，
  自动修正 link 路径（去 .md、加前缀）。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# 脚本位于 <repo>/.codebuddy/skills/docs-manage/scripts/，向上五级到仓库根
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
PLANS_DIR = REPO_ROOT / "docs" / "plans"
CONFIG_FILE = REPO_ROOT / "docs" / ".vitepress" / "config.mts"

# 遗漏文档的默认追加分组标题
DEFAULT_GROUP = "测试与工程优化"


def main() -> int:
    parser = argparse.ArgumentParser(description="校验方案文档是否已同步到 VitePress 侧边栏")
    parser.add_argument("--append", action="store_true", help="将遗漏文档追加到侧边栏默认分组")
    args = parser.parse_args()

    if not PLANS_DIR.is_dir():
        print(f"[error] 目录不存在: {PLANS_DIR}")
        return 1
    if not CONFIG_FILE.is_file():
        print(f"[error] 配置不存在: {CONFIG_FILE}")
        return 1

    # 收集 plans 目录下的文档（排除 _template.md 等以下划线开头的）
    doc_files = sorted(
        p for p in PLANS_DIR.glob("*.md") if not p.name.startswith("_")
    )

    # 读取侧边栏配置文本
    config_text = CONFIG_FILE.read_text(encoding="utf-8")

    # 收集侧边栏中已引用的 plans link（去 .md、加 /plans/ 前缀）
    linked = set()
    for doc in doc_files:
        link = f"/plans/{doc.name[:-3]}"
        # 侧边栏中 link 写法有两种：'/plans/xxx' 或含引号 "/plans/xxx"
        if link in config_text:
            linked.add(doc)

    missing = [d for d in doc_files if d not in linked]

    if not missing:
        print("[ok] 侧边栏已覆盖全部方案文档，无遗漏。")
        return 0

    print(f"[warn] 以下 {len(missing)} 份方案文档未同步到侧边栏：")
    for doc in missing:
        print(f"  - {doc.name}")

    if not args.append:
        print("\n提示：运行 `python sync_sidebar.py --append` 可将它们追加到侧边栏默认分组。")
        return 1

    # 追加模式：将遗漏文档追加到指定分组
    if not _append_to_group(config_text, missing):
        return 1
    return 0


def _append_to_group(config_text: str, missing: list[Path]) -> bool:
    """将遗漏文档追加到 config.mts 中标题为 DEFAULT_GROUP 的分组末尾。"""
    group_pattern = re.compile(
        r"(text:\s*['\"]" + re.escape(DEFAULT_GROUP) + r"['\"][^\]]*?items:\s*\[)([^\]]*?)(\]\s*,?\s*\})",
        re.DOTALL,
    )
    match = group_pattern.search(config_text)
    if not match:
        print(f"[error] 未在 config.mts 中找到分组「{DEFAULT_GROUP}」或格式不匹配。")
        print("       请手动将遗漏文档加入合适分组，或先创建该分组。")
        return False

    head, items_body, tail = match.groups()
    new_items = []
    for doc in missing:
        # 从文件名提取 text（去序号前缀，去 .md）
        stem = doc.stem
        title = re.sub(r"^\d+-", "", stem)
        link = f"/plans/{stem}"
        new_items.append(f"            {{ text: '{title}', link: '{link}' }},")

    # 若分组已有 items，在其末尾（最后一个 item 之后）插入
    if items_body.strip():
        insertion = "\n" + "\n".join(new_items)
    else:
        insertion = "\n" + "\n".join(new_items)

    new_body = items_body.rstrip() + insertion + "\n"
    updated = config_text[: match.start()] + head + new_body + tail + config_text[match.end():]

    CONFIG_FILE.write_text(updated, encoding="utf-8")
    print(f"[ok] 已将 {len(missing)} 份遗漏文档追加到「{DEFAULT_GROUP}」分组。")
    print("注意：追加的分组可能不语义匹配，请人工复核是否需要移动到更合适的分组。")
    return True


if __name__ == "__main__":
    sys.exit(main())
