#!/usr/bin/env python3
"""
guard-branch.py —— 不在保护分支（main/master）上干活；新分支名要合规。

三家 agent 共用同一份：Claude Code / Codex CLI / Pi 的 PreToolUse 协议是一样的
  stdin : 事件 JSON（tool_name / tool_input / cwd ...）
  stdout: {"hookSpecificOutput": {"permissionDecision": "deny", ...}}  → 拦下这次工具调用
          {"hookSpecificOutput": {"permissionDecision": "ask",  ...}}  → 弹确认框，人点了才放行
  exit 0 永远返回，让宿主按 JSON 判断；解析不了就放行，守卫绝不能把会话卡死。

用法：guard-branch.py --host <claude|codex|pi>
  `ask` 只有 Claude Code 认。Codex 官方文档写明 ask "parsed but not supported yet"：hook 记为失败、报错、
  然后**照常执行工具**——也就是合并直接放行。所以只对声明为 claude 的宿主发 ask，其他宿主（含没传 --host 的）
  一律 deny，让人自己在终端合。宁可拦错，不能放过。

逃生门（二选一）：
  env ALLOW_MAIN=1
  touch <repo>/.git/ALLOW_MAIN          （不进版本库，按 clone 隔离）

可调项见 _common.py 顶部（git config autopilot.*）。
"""
import json
import os
import re
import shlex
import subprocess
import sys

from _common import branch_hint, branch_pattern, cfg, enabled, git, protected

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

# 哪些宿主真的会把 permissionDecision: ask 变成确认框。不在这里的一律用 deny 代替。
ASK_HOSTS = {"claude"}

# 合并 PR 的确认框要让人能判断（issue #8）：弹框前先 `gh pr view --json` 查这些字段拼进去
PR_FIELDS = ("number,title,url,state,isDraft,headRefName,baseRefName,commits,"
             "changedFiles,additions,deletions,statusCheckRollup,reviewDecision,mergeable")
# `gh pr merge` 的选项里带参数的那几个，找 PR 选择器（编号 / URL / 分支）时要跳过
MERGE_OPT_WITH_ARG = {"-R", "--repo", "-b", "--body", "-F", "--body-file", "-t", "--subject",
                      "-A", "--author-email", "--match-head-commit"}
MERGE_METHODS = {"-s": "squash", "--squash": "squash", "-m": "merge", "--merge": "merge",
                 "-r": "rebase", "--rebase": "rebase"}
# statusCheckRollup 里各项的结论怎么归类：一项失败整体算失败，没失败但有没跑完的算还在跑
CI_FAILED = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE"}
CI_PASSED = {"SUCCESS", "NEUTRAL", "SKIPPED"}
REVIEW_TEXT = {"APPROVED": "已批准", "CHANGES_REQUESTED": "要求修改", "REVIEW_REQUIRED": "要求 review 但还没人批", "": "没人看过"}
MERGEABLE_TEXT = {"MERGEABLE": "无", "CONFLICTING": "有冲突", "UNKNOWN": "未知（GitHub 还在算）"}

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


def merge_args(seg):
    """解析 `gh pr merge [<编号|URL|分支>] [选项]`，返回 dict：selector / repo / method / delete / auto。"""
    try:
        toks = shlex.split(seg)
    except ValueError:
        toks = seg.split()
    out = {"selector": "", "repo": "", "method": "", "delete": False, "auto": False}
    try:
        i = toks.index("merge", toks.index("gh")) + 1
    except ValueError:
        return out
    while i < len(toks):
        t = toks[i]
        if t in MERGE_METHODS:
            out["method"] = MERGE_METHODS[t]
        elif t in ("-d", "--delete-branch"):
            out["delete"] = True
        elif t == "--auto":
            out["auto"] = True
        elif t.startswith("--repo="):
            out["repo"] = t.split("=", 1)[1]
        elif t in MERGE_OPT_WITH_ARG:
            if t in ("-R", "--repo") and i + 1 < len(toks):
                out["repo"] = toks[i + 1]
            i += 1
        elif not t.startswith("-") and not out["selector"]:
            out["selector"] = t
        i += 1
    return out


def pr_info(cwd, selector, repo):
    """跑 gh pr view 拿 PR 信息。返回 (dict, "") 或 (None, 失败原因)。"""
    try:
        timeout = float(cfg(cwd, "gh-timeout", "5"))
    except ValueError:
        timeout = 5.0
    if timeout <= 0:
        return None, "autopilot.gh-timeout 设成了 0，按约定不查"
    args = ["gh", "pr", "view"] + ([selector] if selector else []) + (["-R", repo] if repo else []) + ["--json", PR_FIELDS]
    try:
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"gh pr view 等了 {timeout:g} 秒没返回（网络慢？可调 git config autopilot.gh-timeout）"
    except Exception as e:  # noqa: BLE001  gh 没装等
        return None, f"跑不了 gh：{e}"
    if r.returncode:
        return None, (r.stderr.strip() or r.stdout.strip() or f"gh 退出码 {r.returncode}").splitlines()[0]
    try:
        return json.loads(r.stdout), ""
    except ValueError:
        return None, "gh 返回的不是 JSON"


def ci_status(rollup):
    """把 statusCheckRollup 归纳成一句：没配 / 通过 / 失败（x/n） / 还在跑（x/n）。"""
    if not rollup:
        return "没配"
    failed = running = 0
    for c in rollup:
        s = (c.get("conclusion") or c.get("state") or "").upper()   # 检查项用 conclusion，状态项用 state
        if not s:
            s = (c.get("status") or "").upper()                     # 没结论的检查项看 status：IN_PROGRESS / QUEUED
        if s in CI_FAILED:
            failed += 1
        elif s not in CI_PASSED:
            running += 1
    n = len(rollup)
    if failed:
        return f"失败（{failed}/{n}）"
    if running:
        return f"还在跑（{running}/{n} 没完）"
    return f"通过（{n}/{n}）"


def pr_summary(cwd, seg, prot, uncertain_context=False):
    """给确认框用的 PR 摘要。查得到就是一段能判断的信息，查不到就明说人在盲判。"""
    a = merge_args(seg)
    if uncertain_context:
        info, err = None, "复合命令的仓库上下文无法可靠确定；请在目标仓库单独执行合并命令"
    else:
        info, err = pr_info(cwd, a["selector"], a["repo"])
    method = a["method"] or "未指定（gh 会问，或按仓库只允许的那种）"
    if a["auto"]:
        method += "，--auto：CI 过了自动合"
    delete = "是" if a["delete"] else "否（没传 --delete-branch）"
    if info is None:
        view = f"gh pr view {a['selector']}".strip()
        return (
            f"agent 要合并 PR：{seg.strip()}\n"
            f"没查到 PR 信息：{err}\n"
            f"你现在是在盲判。建议先在终端跑 {view} 看一眼再决定。\n"
            f"方式：{method}    合并后删远端分支：{delete}"
        )
    base = info.get("baseRefName") or "?"
    base_note = "（保护分支）" if base in prot else ""
    n_commits = len(info.get("commits") or [])
    commits = f"{n_commits}+" if n_commits >= 100 else str(n_commits)   # gh 的 commits 字段只给前 100 个
    lines = [
        f"agent 要合并 PR #{info.get('number')}「{info.get('title', '')}」",
        f"{info.get('url', '')}",
        f"  {info.get('headRefName') or '?'} → {base}{base_note}",
        f"  {commits} 个提交，改了 {info.get('changedFiles', '?')} 个文件，"
        f"+{info.get('additions', '?')} / -{info.get('deletions', '?')}",
        f"  CI：{ci_status(info.get('statusCheckRollup'))}    "
        f"review：{REVIEW_TEXT.get(info.get('reviewDecision') or '', info.get('reviewDecision'))}    "
        f"冲突：{MERGEABLE_TEXT.get(info.get('mergeable') or 'UNKNOWN', info.get('mergeable'))}    "
        f"draft：{'是（gh 会拒绝合 draft）' if info.get('isDraft') else '否'}",
        f"  方式：{method}    合并后删远端分支：{delete}",
    ]
    state = info.get("state") or "OPEN"
    if state != "OPEN":
        lines.append(f"  注意：这个 PR 状态是 {state}，不是打开的，合并会失败")
    lines.append(f"命令：{seg.strip()}")
    return "\n".join(lines)


def host_from_argv(argv):
    """`--host codex` 或 `--host=codex`；没传就是 unknown，按不支持 ask 处理。"""
    for i, a in enumerate(argv):
        if a == "--host" and i + 1 < len(argv):
            return argv[i + 1].strip().lower()
        if a.startswith("--host="):
            return a.split("=", 1)[1].strip().lower()
    return "unknown"


def main():
    try:
        ev = json.load(sys.stdin)
    except Exception:
        allow()

    host = host_from_argv(sys.argv[1:])
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
            summary = pr_summary(cwd, seg, prot, uncertain_context=len(segments) > 1)
            if host in ASK_HOSTS:
                emit(
                    "ask",
                    f"{summary}\n"
                    f"合并后改动就进 {' / '.join(prot)} 了，不好撤。确认要合？",
                )
            emit(
                "deny",
                f"合并 PR 需要人本人确认，但当前宿主（{host}）不支持 hook 弹确认框，所以这里直接拦下。\n"
                f"{summary}\n"
                f"请把这条命令交给人在终端自己执行。合并后改动就进 {' / '.join(prot)} 了，不好撤。"
                + ("\n如果这其实是 Claude Code：hook 配置是旧版装的，没带 --host claude；"
                   "重跑一次 install.py（或更新插件）就会改回弹确认框。" if host == "unknown" else ""),
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

    branch = git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    if not branch or branch not in prot:
        allow()  # 不在 git 仓库里，或已经在工作分支上

    # ---- 逃生门 ----
    if os.environ.get("ALLOW_MAIN") == "1":
        allow()
    common = git(cwd, "rev-parse", "--git-common-dir")  # worktree 里也指向主仓的 .git
    if common and not os.path.isabs(common):
        common = os.path.join(git(cwd, "rev-parse", "--show-toplevel") or cwd, common)
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
