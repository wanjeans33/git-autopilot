#!/usr/bin/env python3
"""
git-autopilot 安装器。跨平台，只依赖 Python 3.8+，没有 bash。

  python3 install.py [仓库路径]              装到一个仓库（默认当前目录）
  python3 install.py --global                装到用户级，所有仓库生效
  python3 install.py --uninstall [仓库路径]   卸载；--uninstall --global 卸用户级
  python3 install.py --uninstall --global --agent=codex  只卸 Codex 旧安装，迁移到插件
  python3 install.py --docs-only [仓库路径]   只写 CLAUDE.md / AGENTS.md 约定段，不碰 hook

Windows 上用 py install.py 或 python install.py，效果一样。

装什么：
  hook 配置   .claude/settings.json  .codex/hooks.json  .pi/settings.json   —— 事后拦、收工兜底
  约定段落   CLAUDE.md  AGENTS.md                                           —— 事前讲，带标记的一段

hook 命令里写的是运行本安装器的这个 Python 的绝对路径（sys.executable），
所以 Windows 上没有 python3 这个名字也没关系。

幂等：重复运行只覆盖本工具自己那部分，不碰你已有的其他 hook 和文档内容。改动前备份成 .bak。
"""
import json
import re
import shutil
import sys
from pathlib import Path

TOOLKIT = Path(__file__).resolve().parent
HOOKS_DIR = TOOLKIT / "hooks"
PYTHON = Path(sys.executable).resolve().as_posix()   # 正斜杠，Windows 的 shell 也认

# 本工具的指纹：命令里带这些脚本名的 hook 组就是我们装的（含旧版本的名字，升级时摘干净）
MARKS = ("guard-branch.py", "wrap-up.py", "nudge-review.py")
BEGIN, END = "<!-- git-autopilot:begin -->", "<!-- git-autopilot:end -->"
BLOCK = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END) + r"[ \t]*\n?", re.S)

# 每家 agent：(hook 配置相对路径, 模板名, 约定文档相对路径 或 None)
PROJECT_TARGETS = [
    (".claude/settings.json", "claude.settings.json", "CLAUDE.md"),
    (".codex/hooks.json",     "codex.hooks.json",     "AGENTS.md"),
    (".pi/settings.json",     "pi.settings.json",     "AGENTS.md"),
]
# 用户级：Claude Code 和 Codex 的全局路径按官方文档；Pi 的全局路径按其文档推断，未实测。
GLOBAL_TARGETS = [
    (".claude/settings.json",   "claude.settings.json", ".claude/CLAUDE.md"),
    (".codex/hooks.json",       "codex.hooks.json",     ".codex/AGENTS.md"),
    (".pi/agent/settings.json", "pi.settings.json",     None),
]


def log(msg):
    print(f"  {msg}")


def backup(path: Path):
    if path.exists():
        shutil.copy(path, path.with_name(path.name + ".bak"))


def mine(group):
    return any(m in h.get("command", "") for h in group.get("hooks", []) for m in MARKS)


def render_template(name):
    text = (TOOLKIT / "templates" / name).read_text(encoding="utf-8")
    return json.loads(text.replace("__PYTHON__", PYTHON).replace("__HOOKS_DIR__", HOOKS_DIR.as_posix()))


def install_hooks(path: Path, template: str, mode: str):
    existing = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            log(f"! {path} 不是合法 JSON，跳过（请先修好）")
            return
        backup(path)

    # 先摘掉本工具旧的那几条
    for ev, groups in list(existing.get("hooks", {}).items()):
        kept = [g for g in groups if not mine(g)]
        if kept:
            existing["hooks"][ev] = kept
        else:
            existing["hooks"].pop(ev, None)
    if not existing.get("hooks"):
        existing.pop("hooks", None)

    if mode == "install":
        new = render_template(template)
        existing.setdefault("hooks", {})
        for ev, groups in new["hooks"].items():
            existing["hooks"].setdefault(ev, []).extend(groups)

    if not existing:
        if path.exists():
            path.unlink()
            log(f"- {path} 已清空并删除")
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"✓ {path} {'已装载' if mode == 'install' else '已摘除'}")


def install_docs(path: Path, mode: str):
    snippet = (TOOLKIT / "templates" / "agent-rules.md").read_text(encoding="utf-8").strip()
    existed = path.exists()
    text = path.read_text(encoding="utf-8") if existed else ""
    stripped = BLOCK.sub("", text)

    if mode == "install":
        new = (stripped.rstrip() + "\n\n" if stripped.strip() else "") + snippet + "\n"
    else:
        new = stripped if stripped.strip() else ""

    if new == text:
        log(f"= {path} 无需改动")
        return
    backup(path)
    if not new:
        path.unlink()
        log(f"- {path} 只剩本工具那段，已删除")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new, encoding="utf-8")
    log(f"✓ {path} {'已写入约定' if mode == 'install' else '已摘除约定'}")


def main(argv):
    mode, scope, docs_only = "install", "project", False
    agent = None
    rest = []
    for a in argv:
        if a == "--uninstall":
            mode = "uninstall"
        elif a == "--global":
            scope = "global"
        elif a == "--docs-only":
            docs_only = True
        elif a.startswith("--agent="):
            agent = a.split("=", 1)[1]
            if agent not in ("claude", "codex", "pi"):
                print("--agent 必须是 claude、codex 或 pi", file=sys.stderr)
                return 1
        elif a in ("-h", "--help"):
            print(__doc__); return 0
        else:
            rest.append(a)

    if scope == "global":
        root = Path.home()
        targets = GLOBAL_TARGETS
        print(f"用户级（{root}），所有仓库生效：")
    else:
        root = Path(rest[0] if rest else ".").resolve()
        if not (root / ".git").exists() and not any(p.name == ".git" for p in root.parents):
            print(f"✗ {root} 不是 git 仓库", file=sys.stderr); return 1
        targets = PROJECT_TARGETS
        print(f"仓库 {root}：")

    if agent:
        targets = [t for t in targets if t[0].startswith("." + agent + "/")]
    seen_docs = set()
    for hook_rel, template, doc_rel in targets:
        if not docs_only:
            install_hooks(root / hook_rel, template, mode)
        # 仓库 AGENTS.md 由 Codex / Pi 共用，单宿主卸载时保留约定。
        keep_shared = mode == "uninstall" and agent and scope == "project" and doc_rel == "AGENTS.md"
        if doc_rel and doc_rel not in seen_docs and not keep_shared:
            seen_docs.add(doc_rel)
            install_docs(root / doc_rel, mode)

    print()
    print(f"脚本位置 : {HOOKS_DIR}")
    print(f"解释器   : {PYTHON}")
    print("逃生门   : ALLOW_MAIN=1  或  touch <仓库>/.git/ALLOW_MAIN")
    if mode == "install":
        print("某仓库关 : git config autopilot.enabled false")
        print("可调项   : git config autopilot.{protected|base|branch-prefixes|branch-pattern|autocommit|autopush|pr-nudge-after}")
        print("注意     : hook 配置在会话启动时快照，已开着的会话要重开才生效")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
