#!/usr/bin/env python3
"""
guard-branch.py —— 不在保护分支（main/master）上干活。

三家 agent 共用同一份：Claude Code / Codex CLI / Pi 的 PreToolUse 协议是一样的
  stdin : 事件 JSON（tool_name / tool_input / cwd ...）
  stdout: {"hookSpecificOutput": {"permissionDecision": "deny", ...}}  → 拦下这次工具调用
  exit 0 永远返回，让宿主按 JSON 判断；解析不了就放行，守卫绝不能把会话卡死。

逃生门（二选一）：
  env ALLOW_MAIN=1
  touch <repo>/.git/ALLOW_MAIN          （不进版本库，按 clone 隔离）
"""
import json
import os
import re
import subprocess
import sys

PROTECTED = {"main", "master"}

# 会改动仓库状态的 git 子命令。注意 switch/checkout/branch 不在内 —— 它们正是补救动作。
GIT_WRITE = re.compile(
    r"\bgit\b[\s\S]*?\b(commit|merge|rebase|push|cherry-pick|revert|am|apply)\b"
)
# reset --hard 单独认，避免误伤 git reset（取消暂存）
GIT_RESET_HARD = re.compile(r"\bgit\b[\s\S]*?\breset\b[\s\S]*?--hard\b")
# 强推保护分支：任何分支上都拦
FORCE_PUSH = re.compile(r"\bgit\b[\s\S]*?\bpush\b[\s\S]*?(--force(?!-with-lease)|(?<![\w-])-f(?![\w-]))")

# 各家改文件的工具名并集
EDIT_TOOLS = {
    "Edit", "Write", "MultiEdit", "NotebookEdit",   # Claude Code
    "apply_patch", "str_replace_editor",            # Codex
    "edit", "write",                                # Pi / 其他小写变体
}
SHELL_TOOLS = {"Bash", "bash", "shell", "local_shell", "run_command", "execute_command"}

# 把 shell 命令按分隔符切开，逐段判断，避免 `cd x && git commit` 漏网
SPLIT = re.compile(r"&&|\|\||;|\n|\|")


def emit(decision, reason):
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )
    sys.exit(0)


def allow():
    sys.exit(0)


def git(cwd, *args):
    try:
        r = subprocess.run(
            ["git", "-C", cwd, *args], capture_output=True, text=True, timeout=5
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def main():
    try:
        ev = json.load(sys.stdin)
    except Exception:
        allow()

    cwd = ev.get("cwd") or os.getcwd()
    tool = ev.get("tool_name") or ""
    ti = ev.get("tool_input") or {}
    cmd = ti.get("command") or "" if tool in SHELL_TOOLS else ""
    segments = SPLIT.split(cmd) if cmd else []

    # ---- 规则 0：强推保护分支，任何分支都拦 ----
    for seg in segments:
        if FORCE_PUSH.search(seg) and re.search(r"\b(main|master|origin/(main|master))\b", seg):
            emit("deny", "拒绝强推保护分支。要覆盖远端请你本人确认后手动执行。")

    branch = git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    if not branch or branch not in PROTECTED:
        allow()  # 不在 git 仓库里，或已经在工作分支上

    # ---- 逃生门 ----
    if os.environ.get("ALLOW_MAIN") == "1":
        allow()
    gitdir = git(cwd, "rev-parse", "--absolute-git-dir")
    root = git(cwd, "rev-parse", "--show-toplevel") or cwd
    for marker in (
        os.path.join(gitdir, "ALLOW_MAIN") if gitdir else "",
        os.path.join(root, ".agent-hooks", "ALLOW_MAIN"),
    ):
        if marker and os.path.exists(marker):
            allow()

    hint = (
        f"当前在保护分支 {branch}，本仓库约定：不在 {branch} 上干活。\n"
        f"先开分支再继续：git switch -c <feat|fix|chore|docs>/<简短说明>\n"
        f"未提交的改动会跟着带到新分支，不会丢，直接接着做就行。\n"
        f"确实必须在 {branch} 上操作：touch .git/ALLOW_MAIN 后重试。"
    )

    # ---- 规则 1：在保护分支上改文件 ----
    if tool in EDIT_TOOLS:
        emit("deny", hint)

    # ---- 规则 2：在保护分支上跑写类 git 命令 ----
    for seg in segments:
        if GIT_WRITE.search(seg) or GIT_RESET_HARD.search(seg):
            emit("deny", hint)

    allow()


if __name__ == "__main__":
    main()
