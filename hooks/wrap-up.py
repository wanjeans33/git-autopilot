#!/usr/bin/env python3
"""
wrap-up.py —— Stop 阶段收工四件事（三家共用，stdin 收 JSON，stdout 回 {"systemMessage": "..."}）：

  1. 自动存档：工作分支上还有未提交改动，就 `git add -A && git commit -m "wip: ..."`。
     保护分支、detached HEAD、merge/rebase 进行中：一律不动。
     关掉：git config autopilot.autocommit false
  2. 自动推送：工作分支领先远端就 `git push -u`，不带 force，推不上只提示。
     关掉：git config autopilot.autopush false
  3. 收工提示：这条分支比基准分支多了多少提交、多少文件，合并前建议先审。
  4. PR 提议：攒够提交（autopilot.pr-nudge-after，默认 3）且没开 PR，提一句"建议向人提议提交 PR"。
     这里不会自己开 PR，更不会合。
  5. 分支总览：本地工作分支攒到 autopilot.branch-overview-after（默认 3）条以上，顺带列出其他分支里
     需要注意的：已合并可删、有 wip、远端已删、未推送。完整表格用 hooks/branch-status.py。

几件事放同一个脚本，是因为宿主会把同一事件的多个 hook 并行跑，拆开会让提示数漏掉刚存档的那次。
不拦任何东西，只是给人看一句话。
"""
import json
import os
import shutil
import subprocess
import sys

from _common import base_branch, cfg, cfg_bool, enabled, git, git_rc, protected

try:
    ev = json.load(sys.stdin)
except Exception:
    ev = {}
cwd = ev.get("cwd") or os.getcwd()

branch = git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
if not branch or branch == "HEAD" or branch in protected(cwd) or not enabled(cwd):
    sys.exit(0)

msgs = []

# ---- 1. 自动存档 ----
gitdir = git(cwd, "rev-parse", "--absolute-git-dir")
in_progress = gitdir and any(
    os.path.exists(os.path.join(gitdir, p))
    for p in ("MERGE_HEAD", "REBASE_HEAD", "rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD", "REVERT_HEAD")
)
dirty = git(cwd, "status", "--porcelain")
if dirty and cfg_bool(cwd, "autocommit", True) and not in_progress:
    # 每行是 "XY path"；git() 会 strip 整段输出，首行的前导空格可能没了，所以按空白切而不是按偏移切
    files = [parts[1] for parts in (line.split(None, 1) for line in dirty.splitlines()) if len(parts) == 2]
    head = ", ".join(files[:4]) + (f" 等 {len(files)} 个文件" if len(files) > 4 else "")
    body = "\n".join(files)
    git(cwd, "add", "-A", timeout=30)
    rc, _, err = git_rc(cwd, "commit", "-q", "-m", f"wip: 收工自动存档 — {head}\n\n{body}", timeout=30)
    if rc == 0:
        msgs.append(f"已自动存档 {len(files)} 个文件的改动为 wip 提交（合并前记得整理）。")
    else:
        msgs.append(f"自动存档失败：{err.splitlines()[-1] if err else '未知错误'}。改动仍在工作区。")
elif dirty and in_progress:
    msgs.append("有 merge/rebase 正在进行，未自动存档。")

# ---- 2. 自动推送：工作分支推到远端同名分支，不带 force ----
# 没远端就跳过；远端已是最新就跳过；推不上去（比如本地整理过历史）只提示，不强推。
if cfg_bool(cwd, "autopush", True):
    remote = git(cwd, "config", f"branch.{branch}.remote") or "origin"
    if remote in git(cwd, "remote").split():
        upstream = git(cwd, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        ahead_of_remote = git(cwd, "rev-list", "--count", "@{u}..HEAD") if upstream else "1"
        if ahead_of_remote != "0":
            rc, _, err = git_rc(
                cwd, "push", "-u", remote, branch, timeout=45,
                env={"GIT_TERMINAL_PROMPT": "0"},  # 没凭据就直接失败，别挂在密码提示上
            )
            if rc == 0:
                msgs.append(f"已推到 {remote}/{branch}。")
            else:
                lines = err.splitlines() or ["未知错误"]
                why = next((l for l in lines if "rejected" in l or l.startswith(("error:", "fatal:"))), lines[-1])
                hint = " 本地历史改过？自己分支可 git push --force-with-lease。" if "rejected" in err else ""
                msgs.append(f"推送失败：{why.strip()}。{hint}")

# ---- 3. 收工提示 ----
base = base_branch(cwd)
ahead = git(cwd, "rev-list", "--count", f"{base}..HEAD")
if ahead.isdigit() and int(ahead) > 0:
    files = git(cwd, "diff", "--name-only", f"{base}...HEAD")
    n_files = len([f for f in files.splitlines() if f])
    line = f"分支 {branch} 已比 {base} 多 {ahead} 个提交、{n_files} 个文件。"
    if git(cwd, "status", "--porcelain"):
        line += " 还有未提交改动。"
    line += " 合并前建议先跑 /code-review。"
    msgs.append(line)

    # ---- 4. PR 提议：还没开 PR 且攒够了提交，就提一句。开不开由人定，这里不会自己开。----
    threshold = cfg(cwd, "pr-nudge-after", "3")
    if shutil.which("gh") and threshold.isdigit() and int(ahead) >= int(threshold) and git(cwd, "remote"):
        try:
            r = subprocess.run(
                ["gh", "pr", "view", "--json", "number,url", "--jq", ".number, .url"],
                cwd=cwd, capture_output=True, text=True, timeout=10,
                env={**os.environ, "GH_PROMPT_DISABLED": "1", "GIT_TERMINAL_PROMPT": "0"},
            )
            if r.returncode == 0 and r.stdout.strip():
                num, url = (r.stdout.split() + ["", ""])[:2]
                msgs.append(f"已有 PR #{num}：{url}")
            elif "no pull requests found" in r.stderr.lower():
                msgs.append(
                    f"这条分支还没开 PR。若已到阶段性节点，建议向人提议提交 PR"
                    f"（gh pr create --base {base} --fill），由人决定。"
                )
            # 其他失败（没登录、没网）：不猜，不提
        except Exception:
            pass

# ---- 5. 分支总览：其他分支里有该清理的，提一句。不查 gh（收工要快），完整表格让人自己跑。----
threshold = cfg(cwd, "branch-overview-after", "3")
work_branches = [
    b for b in git(cwd, "for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines()
    if b and b not in protected(cwd)
]
if threshold.isdigit() and len(work_branches) >= int(threshold):
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "branch-status.py")
    try:
        r = subprocess.run([sys.executable, script, "--brief"], cwd=cwd, capture_output=True, text=True, timeout=15)
        others = [l for l in r.stdout.splitlines() if l and not l.startswith(f"{branch}：")]
    except Exception:
        others = []
    if others:
        msgs.append(f"其他分支：{'；'.join(others)}。完整总览：python3 {script}")

if msgs:
    json.dump({"systemMessage": " ".join(msgs)}, sys.stdout, ensure_ascii=False)
