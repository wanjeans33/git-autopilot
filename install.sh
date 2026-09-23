#!/usr/bin/env bash
# 把分支守卫装进一个目标仓库（三家 agent 一次装完）。
#   ./install.sh [目标仓库路径]      默认当前目录
#   ./install.sh --uninstall [路径]  只摘掉本工具的 hook，保留其他配置
#
# 幂等：重复运行只会覆盖本工具自己那条，不碰你已有的其他 hook。
set -euo pipefail

TOOLKIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE=install
if [[ "${1:-}" == "--uninstall" ]]; then MODE=uninstall; shift; fi
TARGET="$(cd "${1:-$PWD}" && pwd)"

if ! git -C "$TARGET" rev-parse --git-dir >/dev/null 2>&1; then
  echo "✗ $TARGET 不是 git 仓库" >&2; exit 1
fi

python3 - "$TOOLKIT" "$TARGET" "$MODE" <<'PYEOF'
import json, os, shutil, sys

toolkit, target, mode = sys.argv[1], sys.argv[2], sys.argv[3]
hooks_dir = os.path.join(toolkit, "hooks")

# (子目录, 配置文件名, 模板名)
AGENTS = [
    (".claude", "settings.json", "claude.settings.json"),
    (".codex",  "hooks.json",    "codex.hooks.json"),
    (".pi",     "settings.json", "pi.settings.json"),
]
MARK = "guard-branch.py"          # 本工具的指纹
MARK2 = "nudge-review.py"


def mine(entry):
    """这条 hook 组是不是本工具装的"""
    for h in entry.get("hooks", []):
        if MARK in h.get("command", "") or MARK2 in h.get("command", ""):
            return True
    return False


for sub, fname, tpl in AGENTS:
    path = os.path.join(target, sub, fname)
    existing = {}
    if os.path.exists(path):
        try:
            existing = json.load(open(path))
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
        new = json.loads(
            open(os.path.join(toolkit, "templates", tpl)).read()
            .replace("__HOOKS_DIR__", hooks_dir)
        )
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

print(f"\n脚本位置: {hooks_dir}")
print("逃生门  : ALLOW_MAIN=1  或  touch <仓库>/.git/ALLOW_MAIN")
if mode == "install":
    print("注意    : Claude Code 若本会话启动时还没有 .claude/ 配置，可能需重开会话才生效")
PYEOF
