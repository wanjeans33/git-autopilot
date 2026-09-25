#!/usr/bin/env python3
"""
branch-status.py —— 分支状态总览。只读：不 fetch（除非 --fetch）、不改任何分支、不碰工作区。

  python3 hooks/branch-status.py            本地分支 + 只在远端有的分支，一行一条
  python3 hooks/branch-status.py --fetch    先 git fetch --prune 再列（唯一会联网的开关）
  python3 hooks/branch-status.py --no-gh    不查 PR / CI（gh 没装、没登录时会自动降级，等价于这个）
  python3 hooks/branch-status.py --brief    只列需要注意的分支，一行一条，收工提示用；不查 gh
  python3 hooks/branch-status.py --json     机器可读

每条分支给出：相对基准分支（autopilot.base，默认 main）的领先 / 落后提交数、和远端同名分支是否同步、
关联 PR 及其评审 / CI 状态、是否已合并可删、有没有 wip 提交、当前分支有没有未提交改动、
是否在别的 worktree 里签出。

子进程次数：for-each-ref 两次（本地、远端）+ 每条分支一次 rev-list（领先 / 落后）
+ 有新提交的分支各一次 git log（数 wip）+ gh 一次。
"""
import json
import os
import shutil
import subprocess
import sys
import time
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import base_branch, git, git_rc, protected  # noqa: E402

SEP = "\x1f"
# 最后一个字段必须非空：git() 会 strip 整段输出，而 \x1f 在 Python 里算空白，末尾的空字段会被吃掉
REF_FMT = SEP.join([
    "%(refname:short)", "%(HEAD)", "%(upstream:short)", "%(upstream:track)",
    "%(committerdate:unix)", "%(subject)", "%(symref)", "%(objecttype)",
])


# ---------- 采集 ----------

def parse_refs(text):
    """for-each-ref 的输出 → 列表。symref 非空的是 origin/HEAD 这类指针，跳过。"""
    out = []
    for line in text.splitlines():
        parts = line.split(SEP)
        if len(parts) < 8 or parts[6]:
            continue
        name, head, upstream, track, date, subject = parts[:6]
        out.append({
            "name": name,
            "current": head == "*",
            "upstream": upstream,
            "track": parse_track(upstream, track),
            "date": int(date) if date.isdigit() else 0,
            "subject": subject,
        })
    return out


def parse_track(upstream, track):
    """%(upstream:track) → same / ahead / behind / diverged / gone / none。"""
    if not upstream:
        return "none"
    t = track.strip("[]")
    if t == "gone":
        return "gone"
    if not t:
        return "same"
    ahead, behind = "ahead" in t, "behind" in t
    return "diverged" if ahead and behind else "ahead" if ahead else "behind" if behind else "same"


def parse_ahead_behind(text):
    """git rev-list --left-right --count base...ref 的 "behind\\tahead" → (ahead, behind)。"""
    parts = text.split()
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None, None
    return int(parts[1]), int(parts[0])


def parse_worktrees(text, here):
    """git worktree list --porcelain → {分支名: 路径}，只收不是当前目录的那些。"""
    def same(a, b):   # macOS 的 /tmp 是 /private/tmp 的符号链接，worktree list 和 show-toplevel 可能一个走链接一个走实路径
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))

    out, path = {}, ""
    for line in text.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line.startswith("branch refs/heads/") and path and not same(path, here):
            out[line[len("branch refs/heads/"):]] = path
    return out


def count_wip(subjects):
    return sum(1 for s in subjects if s.lower().startswith("wip:"))


def collect(cwd, base=None, use_gh=True, fetch=False):
    """采集所有分支状态。返回 (branches, meta)。"""
    if fetch:
        git_rc(cwd, "fetch", "--prune", "--quiet", timeout=60, env={"GIT_TERMINAL_PROMPT": "0"})
    base = base or base_branch(cwd)
    top = git(cwd, "rev-parse", "--show-toplevel") or cwd
    guard = set(protected(cwd)) | {base}

    local = parse_refs(git(cwd, "for-each-ref", f"--format={REF_FMT}", "refs/heads"))
    local_names = {b["name"] for b in local}
    remote = []
    for b in parse_refs(git(cwd, "for-each-ref", f"--format={REF_FMT}", "refs/remotes")):
        short = b["name"].split("/", 1)[1] if "/" in b["name"] else b["name"]
        if short not in local_names and short not in guard:
            b["short"] = short
            remote.append(b)

    worktrees = parse_worktrees(git(cwd, "worktree", "list", "--porcelain"), top)
    dirty = bool(git(cwd, "status", "--porcelain"))
    base_exists = bool(git(cwd, "rev-parse", "--verify", "-q", f"refs/heads/{base}"))

    for b in local:
        b["kind"] = "local"
        b["short"] = b["name"]
    for b in remote:
        b["kind"] = "remote"
        b["current"] = False
    branches = local + remote

    for b in branches:
        b["base"] = b["kind"] == "local" and b["name"] == base
        b["ahead"] = b["behind"] = None
        if base_exists and not b["base"]:
            b["ahead"], b["behind"] = parse_ahead_behind(
                git(cwd, "rev-list", "--left-right", "--count", f"{base}...{b['name']}"))
        b["wip"] = count_wip(git(cwd, "log", "--format=%s", f"{base}..{b['name']}").splitlines()) \
            if b["ahead"] else 0
        b["merged"] = base_exists and not b["base"] and b["ahead"] == 0
        b["dirty"] = b["current"] and dirty
        b["worktree"] = worktrees.get(b["name"], "") if b["kind"] == "local" else ""
        b["pr"] = None

    gh_ok = False
    if use_gh:
        prs = fetch_prs(cwd)
        if prs is not None:
            gh_ok = True
            for b in branches:
                b["pr"] = pick_pr(prs, b["short"])

    order = {True: 0, False: 1}
    branches.sort(key=lambda b: (order[b["base"]], order[b["current"]], order[b["kind"] == "local"], -b["date"]))
    return branches, {"base": base, "base_exists": base_exists, "gh": gh_ok, "cwd": top}


def fetch_prs(cwd):
    """所有 PR（含已合并 / 关闭）。gh 没装、没登录、没网 → None。"""
    if not shutil.which("gh") or not git(cwd, "remote"):
        return None
    try:
        r = subprocess.run(
            ["gh", "pr", "list", "--state", "all", "--limit", "200", "--json",
             "number,state,headRefName,url,reviewDecision,statusCheckRollup,isDraft"],
            cwd=cwd, capture_output=True, text=True, timeout=15,
            env={**os.environ, "GH_PROMPT_DISABLED": "1", "GIT_TERMINAL_PROMPT": "0"},
        )
        if r.returncode != 0:
            return None
        return json.loads(r.stdout or "[]")
    except Exception:  # noqa: BLE001
        return None


def pick_pr(prs, branch):
    """同一分支可能开过多个 PR：有 open 的取 open，否则取编号最大的。"""
    mine = [p for p in prs if p.get("headRefName") == branch]
    if not mine:
        return None
    opened = [p for p in mine if p.get("state") == "OPEN"]
    p = opened[0] if opened else max(mine, key=lambda x: x.get("number", 0))
    return {
        "number": p.get("number"),
        "state": (p.get("state") or "").lower(),
        "draft": bool(p.get("isDraft")),
        "url": p.get("url", ""),
        "review": (p.get("reviewDecision") or "").lower(),
        "ci": summarize_ci(p.get("statusCheckRollup") or []),
    }


FAIL = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}
OK = {"SUCCESS", "NEUTRAL", "SKIPPED"}


def summarize_ci(rollup):
    """statusCheckRollup → ok / fail / pending / none。CheckRun 看 conclusion，StatusContext 看 state。"""
    if not rollup:
        return "none"
    states = set()
    for c in rollup:
        v = (c.get("conclusion") or c.get("state") or "").upper()
        if v in FAIL:
            states.add("fail")
        elif v in OK:
            states.add("ok")
        else:
            states.add("pending")   # 还没结论：IN_PROGRESS / QUEUED / PENDING / EXPECTED / 空
    return "fail" if "fail" in states else "pending" if "pending" in states else "ok"


# ---------- 呈现 ----------

def notes(b, base):
    """备注列：需要人注意的事，按重要性排。"""
    n = []
    pr = b.get("pr")
    if pr and pr["state"] == "merged":
        n.append("PR 已合并，可删远端分支" if b["kind"] == "remote" else "PR 已合并，可删")
    elif b["merged"] and not b["current"]:
        n.append(("已合并，可删远端分支" if b["kind"] == "remote" else "已合并，可删") if b["behind"]
                 else f"与 {base} 相同")
    elif b["merged"] and b["current"]:
        n.append("尚无新提交")
    if b["wip"]:
        n.append(f"wip 提交 {b['wip']} 个")
    if b["dirty"]:
        n.append("未提交改动")
    if b["kind"] == "local":
        if b["track"] == "none" and not b["merged"]:
            n.append("未推送")
        elif b["track"] == "gone":
            n.append("远端分支已删")
        elif b["track"] in ("ahead", "diverged"):
            n.append("本地领先远端")
    if b["worktree"]:
        n.append(f"在 worktree {os.path.basename(b['worktree'])}")
    if pr and pr["state"] == "open":
        if pr["review"] == "changes_requested":
            n.append("PR 被要求修改")
        if pr["ci"] == "fail":
            n.append("CI 失败")
    return n


def attention(b, base):
    """--brief：只留需要动手的事；"和 main 相同"、"在别的 worktree" 这类只是信息，收工时不提。"""
    return [x for x in notes(b, base)
            if x not in (f"与 {base} 相同", "尚无新提交") and not x.startswith("在 worktree ")]


def ago(ts):
    if not ts:
        return "-"
    d = max(0, int(time.time()) - ts)
    if d < 3600:
        return f"{d // 60} 分钟前"
    if d < 86400:
        return f"{d // 3600} 小时前"
    return f"{d // 86400} 天前"


def dwidth(s):
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def pad(s, w):
    return s + " " * max(0, w - dwidth(s))


def clip(s, w):
    out = ""
    for ch in s:
        if dwidth(out + ch) > w - 1:
            return out + "…"
        out += ch
    return out


def row(b, meta):
    base = meta["base"]
    name = ("* " if b["current"] else "  ") + b["name"]
    if b["base"]:
        vs = "基准"
    elif b["ahead"] is None:
        vs = "-"
    else:
        vs = f"+{b['ahead']} -{b['behind']}"
    if b["kind"] == "remote":
        remote = "仅远端"
    else:
        remote = {"none": "未推送", "same": "同步", "ahead": "领先", "behind": "落后",
                  "diverged": "分叉", "gone": "已删"}[b["track"]]
    pr = b.get("pr")
    if not meta["gh"]:
        prcol = cicol = "-"
    elif not pr:
        prcol, cicol = "无", "-"
    else:
        prcol = f"#{pr['number']} {pr['state']}" + (" 草稿" if pr["draft"] else "")
        cicol = {"ok": "通过", "fail": "失败", "pending": "进行中", "none": "-"}[pr["ci"]]
    last = f"{ago(b['date'])} {clip(b['subject'], 28)}"
    return [name, vs, remote, prcol, cicol, last, "；".join(notes(b, base))]


HEAD = ["分支", "相对基准", "远端", "PR", "CI", "最近提交", "备注"]


def render_table(branches, meta, color):
    rows = [row(b, meta) for b in branches]
    widths = [max(dwidth(r[i]) for r in [HEAD] + rows) for i in range(len(HEAD))]
    lines = ["  ".join(pad(h, w) for h, w in zip(HEAD, widths)).rstrip()]
    for b, r in zip(branches, rows):
        line = "  ".join(pad(c, w) for c, w in zip(r, widths)).rstrip()
        if color:
            if b["current"]:
                line = f"\033[1m{line}\033[0m"
            elif r[6]:
                line = f"\033[33m{line}\033[0m"
        lines.append(line)
    n_local = sum(1 for b in branches if b["kind"] == "local")
    tail = f"基准 {meta['base']}，本地 {n_local} 条，仅远端 {len(branches) - n_local} 条。"
    if not meta["gh"]:
        tail += " PR / CI 未查（gh 未装或未登录，或 --no-gh / --brief）。"
    if not meta["base_exists"]:
        tail += f" 本地没有 {meta['base']} 分支，相对基准一列空着。"
    lines.append(tail)
    return "\n".join(lines)


def render_brief(branches, meta):
    out = []
    for b in branches:
        a = attention(b, meta["base"])
        if a:
            out.append(f"{b['name']}：{'，'.join(a)}")
    return "\n".join(out)


def main(argv):
    fetch = "--fetch" in argv
    use_gh = "--no-gh" not in argv and "--brief" not in argv
    cwd = os.getcwd()
    if not git(cwd, "rev-parse", "--git-dir"):
        print("不是 git 仓库。", file=sys.stderr)
        return 1
    branches, meta = collect(cwd, use_gh=use_gh, fetch=fetch)
    if "--json" in argv:
        json.dump({"meta": meta, "branches": branches}, sys.stdout, ensure_ascii=False, indent=2)
        print()
    elif "--brief" in argv:
        text = render_brief(branches, meta)
        if text:
            print(text)
    else:
        color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
        print(render_table(branches, meta, color))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
