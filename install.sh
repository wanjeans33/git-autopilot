#!/usr/bin/env bash
# 把分支守卫装进一个目标仓库（三家 agent 一次装完）。
#   ./install.sh [目标仓库路径]      默认当前目录
#   ./install.sh --uninstall [路径]  只摘掉本工具的 hook 和文档段落，保留其他内容
#
# 装什么：
#   .claude/settings.json  .codex/hooks.json  .pi/settings.json   —— hook（事后拦）
#   CLAUDE.md  AGENTS.md                                           —— 约定（事前讲），带标记的一段
#
# 幂等：重复运行只会覆盖本工具自己那部分，不碰你已有的其他 hook 和文档内容。
set -euo pipefail

TOOLKIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE=install
if [[ "${1:-}" == "--uninstall" ]]; then MODE=uninstall; shift; fi
TARGET="$(cd "${1:-$PWD}" && pwd)"

if ! git -C "$TARGET" rev-parse --git-dir >/dev/null 2>&1; then
  echo "✗ $TARGET 不是 git 仓库" >&2; exit 1
fi

python3 - "$TOOLKIT" "$TARGET" "$MODE" <<'PYEOF'
import json, os, re, shutil, sys

toolkit, target, mode = sys.argv[1], sys.argv[2], sys.argv[3]
hooks_dir = os.path.join(toolkit, "hooks")

# ---------------- hook 配置 ----------------
# (子目录, 配置文件名, 模板名)
AGENTS = [
    (".claude", "settings.json", "claude.settings.json"),
    (".codex",  "hooks.json",    "codex.hooks.json"),
    (".pi",     "settings.json", "pi.settings.json"),
]
# 本工具的指纹：命令里带这些脚本名的 hook 组就是我们装的（含旧版本的名字，方便升级时摘干净）
MARKS = ("guard-branch.py", "wrap-up.py", "nudge-review.py")


def mine(entry):
    return any(m in h.get("command", "") for h in entry.get("hooks", []) for m in MARKS)


for sub, fname, tpl in AGENTS:
    path = os.path.join(target, sub, fname)
    existing = {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                existing = json.load(f)
        except Exception:
            print(f"  ! {sub}/{fname} 不是合法 JSON，跳过（请先修好）")
            continue
        shutil.copy(path, path + ".bak")

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
        with open(os.path.join(toolkit, "templates", tpl)) as f:
            new = json.loads(f.read().replace("__HOOKS_DIR__", hooks_dir))
        existing.setdefault("hooks", {})
        for ev, groups in new["hooks"].items():
            existing["hooks"].setdefault(ev, []).extend(groups)

    if not existing:
        if os.path.exists(path):
            os.remove(path)
            print(f"  - {sub}/{fname} 已清空并删除")
        continue

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
        f.write("\n")
    verb = "已装载" if mode == "install" else "已摘除"
    print(f"  ✓ {sub}/{fname} {verb}")

# ---------------- 约定文档 ----------------
# Claude Code 读 CLAUDE.md，Codex 和 Pi 读 AGENTS.md。同一段内容，两份都写。
DOCS = ["CLAUDE.md", "AGENTS.md"]
BEGIN, END = "<!-- git-autopilot:begin -->", "<!-- git-autopilot:end -->"
BLOCK = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END) + r"[ \t]*\n?", re.S)
with open(os.path.join(toolkit, "templates", "agent-rules.md")) as f:
    snippet = f.read().strip()

for name in DOCS:
    path = os.path.join(target, name)
    existed = os.path.exists(path)
    text = open(path).read() if existed else ""
    stripped = BLOCK.sub("", text)

    if mode == "install":
        new = (stripped.rstrip() + "\n\n" if stripped.strip() else "") + snippet + "\n"
    else:
        new = stripped if stripped.strip() else ""

    if new == text:
        print(f"  = {name} 无需改动")
        continue
    if existed:
        shutil.copy(path, path + ".bak")
    if not new:
        os.remove(path)
        print(f"  - {name} 只剩本工具那段，已删除")
        continue
    with open(path, "w") as f:
        f.write(new)
    verb = "已写入约定" if mode == "install" else "已摘除约定"
    print(f"  ✓ {name} {verb}")

print(f"\n脚本位置: {hooks_dir}")
print("逃生门  : ALLOW_MAIN=1  或  touch <仓库>/.git/ALLOW_MAIN")
if mode == "install":
    print("可调项  : git config autopilot.{protected|base|branch-prefixes|branch-pattern|autocommit|autopush}")
    print("注意    : Claude Code 若本会话启动时还没有 .claude/ 配置，可能需重开会话才生效")
PYEOF
