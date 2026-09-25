#!/usr/bin/env python3
"""
guard-branch.py —— 不在保护分支（main/master）上干活；新分支名要合规。

三家 agent 共用同一份：Claude Code / Codex CLI / Pi 的 PreToolUse 协议是一样的
  stdin : 事件 JSON（tool_name / tool_input / cwd ...）
  stdout: {"hookSpecificOutput": {"permissionDecision": "deny", ...}}  → 拦下这次工具调用
          {"hookSpecificOutput": {"permissionDecision": "ask",  ...}}  → 弹确认框，人点了才放行
  exit 0 永远返回，让宿主按 JSON 判断；解析不了就放行，守卫绝不能把会话卡死。

逃生门（二选一）：
  env ALLOW_MAIN=1
  touch <repo>/.git/ALLOW_MAIN          （不进版本库，按 clone 隔离）

可调项见 _common.py 顶部（git config autopilot.*）。
"""
import json
import os
import re
import shlex
import sys

from _common import branch_hint, branch_pattern, enabled, git, protected

# 会改动仓库状态的 git 子命令。注意 switch/checkout/branch 不在内 —— 它们正是补救动作。
GIT_WRITE = re.compile(
    r"\bgit\b[\s\S]*?\b(commit|merge|rebase|push|cherry-pick|revert|am|apply)\b"
)
# reset --hard 单独认，避免误伤 git reset（取消暂存）
GIT_RESET_HARD = re.compile(r"\bgit\b[\s\S]*?\breset\b[\s\S]*?--hard\b")
# 强推保护分支：任何分支上都拦
FORCE_PUSH = re.compile(r"\bgit\b[\s\S]*?\bpush\b[\s\S]*?(--force(?!-with-lease)|(?<![\w-])-f(?![\w-]))")
# 合并 PR：任何分支上都弹确认框，人点了才合。开 PR 不问（约定里要求 agent 先提议）。
GH_MERGE = re.compile(r"\bgh\b[\s\S]*?\bpr\b[\s\S]*?\bmerge\b")

# 各家改文件的工具名并集
EDIT_TOOLS = {
    "Edit", "Write", "MultiEdit", "NotebookEdit",   # Claude Code
    "apply_patch", "str_replace_editor",            # Codex
    "edit", "write",                                # Pi / 其他小写变体
}
SHELL_TOOLS = {"Bash", "bash", "shell", "local_shell", "run_command", "execute_command"}

# 把 shell 命令按分隔符切开，逐段判断，避免 `cd x && git commit` 漏网
SPLIT = re.compile(r"&&|\|\||;|\n|\|")

# git 的全局选项里带参数的那几个，找子命令时要跳过
GIT_GLOBAL_WITH_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
# `git branch` 带这些选项时不是在建分支
BRANCH_NOT_CREATE = {
    "-d", "-D", "--delete", "-m", "-M", "--move", "-c", "-C", "--copy",
    "-l", "--list", "-a", "--all", "-r", "--remotes", "--show-current",
    "-u", "--set-upstream-to", "--unset-upstream", "--edit-description",
    "--contains", "--no-contains", "--merged", "--no-merged", "--points-at",
}


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


def new_branch_names(seg):
    """从一段 shell 命令里找出 `git switch -c X` / `git checkout -b X` / `git branch X` 要建的分支名。"""
    try:
        toks = shlex.split(seg)
    except ValueError:
        toks = seg.split()
    if "git" not in toks:
        return []
    i = toks.index("git") + 1
    while i < len(toks) and toks[i].startswith("-"):
        i += 2 if toks[i] in GIT_GLOBAL_WITH_ARG else 1
    if i >= len(toks):
        return []
    sub, rest = toks[i], toks[i + 1:]
    names = []
    if sub in ("switch", "checkout"):
        flags = ("-c", "-C", "--create", "--force-create") if sub == "switch" else ("-b", "-B")
        for j, t in enumerate(rest):
            if t in flags and j + 1 < len(rest):
                names.append(rest[j + 1])
    elif sub == "branch":
        if any(t in BRANCH_NOT_CREATE for t in rest):
            return []
        positional = [t for t in rest if not t.startswith("-")]
        if positional:
            names.append(positional[0])
    return names


def main():
    try:
        ev = json.load(sys.stdin)
    except Exception:
        allow()

    cwd = ev.get("cwd") or os.getcwd()
    if not enabled(cwd):
        allow()  # 这个仓库明确说不要
    tool = ev.get("tool_name") or ""
    ti = ev.get("tool_input") or {}
    cmd = ti.get("command") or "" if tool in SHELL_TOOLS else ""
    segments = SPLIT.split(cmd) if cmd else []
    prot = protected(cwd)
    prot_re = r"\b(%s)\b" % "|".join(re.escape(b) for b in prot)

    # ---- 规则 0：强推保护分支，任何分支都拦 ----
    for seg in segments:
        if FORCE_PUSH.search(seg) and re.search(prot_re, seg):
            emit("deny", "拒绝强推保护分支。要覆盖远端请你本人确认后手动执行。")
        if GH_MERGE.search(seg):
            emit(
                "ask",
                f"agent 要合并 PR：{seg.strip()}\n"
                f"合并后改动就进 {' / '.join(prot)} 了，不好撤。确认要合？",
            )

    # ---- 规则 1：新建分支必须符合命名约定，任何分支都查 ----
    pattern = branch_pattern(cwd)
    for seg in segments:
        for name in new_branch_names(seg):
            if not re.match(pattern, name):
                emit(
                    "deny",
                    f"分支名 \"{name}\" 不符合本仓库约定，正则：{pattern}\n"
                    f"照这个来：{branch_hint(cwd)}\n"
                    f"例：feat/login-form  fix/null-deref  chore/bump-deps\n"
                    f"改规则：git config autopilot.branch-prefixes 'feat fix ...' "
                    f"或 git config autopilot.branch-pattern '<正则>'",
                )

    # branch --show-current 在刚 init、还没有任何提交的"未出生"分支上也能给出分支名；
    # rev-parse --abbrev-ref HEAD 那时会报错，拿到空串就等于把新仓库的 main 放开了。
    # 它也不会因为有个同名 tag 就把 main 报成 heads/main（--abbrev-ref 和 symbolic-ref --short 都会）。
    # detached HEAD、不在 git 仓库里：都是空串，放行。
    branch = git(cwd, "branch", "--show-current")
    if not branch or branch not in prot:
        allow()  # 不在 git 仓库里、detached HEAD，或已经在工作分支上

    # ---- 逃生门 ----
    if os.environ.get("ALLOW_MAIN") == "1":
        allow()
    common = git(cwd, "rev-parse", "--git-common-dir")  # worktree 里也指向主仓的 .git
    if common and not os.path.isabs(common):
        # git 输出的相对路径是相对 cwd 的（从子目录调用会是 ../.git），不是相对仓库根
        common = os.path.normpath(os.path.join(cwd, common))
    root = git(cwd, "rev-parse", "--show-toplevel") or cwd
    for marker in (
        os.path.join(common, "ALLOW_MAIN") if common else "",
        os.path.join(root, ".agent-hooks", "ALLOW_MAIN"),
    ):
        if marker and os.path.exists(marker):
            allow()

    hint = (
        f"当前在保护分支 {branch}，本仓库约定：不在 {branch} 上干活。\n"
        f"先开分支再继续：{branch_hint(cwd)}\n"
        f"未提交的改动会跟着带到新分支，不会丢，直接接着做就行。\n"
        f"确实必须在 {branch} 上操作：touch .git/ALLOW_MAIN 后重试。"
    )

    # ---- 规则 2：在保护分支上改文件 ----
    if tool in EDIT_TOOLS:
        emit("deny", hint)

    # ---- 规则 3：在保护分支上跑写类 git 命令 ----
    for seg in segments:
        if GIT_WRITE.search(seg) or GIT_RESET_HARD.search(seg):
            emit("deny", hint)

    allow()


if __name__ == "__main__":
    main()
