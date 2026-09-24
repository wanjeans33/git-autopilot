"""
_common.py —— 几个 hook 共用的小函数和配置读取。

所有可调项都走 git config，作用域按仓库（--local）或全局（--global）：
  autopilot.protected       保护分支，空格或逗号分隔。默认 "main master"
  autopilot.base            收工提示的比较基准。默认取 protected 里第一个实际存在的分支
  autopilot.branch-pattern  新建分支名必须匹配的正则。默认由 branch-prefixes 生成
  autopilot.branch-prefixes 允许的分支前缀，空格分隔。默认 "feat fix chore docs refactor test"
  autopilot.autocommit      收工时是否自动把未提交改动存档成 wip 提交。默认 true
  autopilot.autopush        收工时是否自动把工作分支推到远端（不带 force）。默认 true
  autopilot.pr-nudge-after  领先基准分支多少个提交后，收工提示里开始建议提 PR。默认 3
"""
import os
import re
import subprocess

DEFAULT_PROTECTED = ["main", "master"]
DEFAULT_PREFIXES = "feat fix chore docs refactor test"


def git(cwd, *args, timeout=5):
    """成功返回 stdout（去空白），失败或超时返回空串。"""
    rc, out, _ = git_rc(cwd, *args, timeout=timeout)
    return out if rc == 0 else ""


def git_rc(cwd, *args, timeout=5, env=None):
    """返回 (returncode, stdout, stderr)。异常时 returncode 为 -1。"""
    try:
        r = subprocess.run(
            ["git", "-C", cwd, *args], capture_output=True, text=True, timeout=timeout,
            env={**os.environ, **env} if env else None,
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:  # noqa: BLE001
        return -1, "", str(e)


def cfg(cwd, key, default=""):
    v = git(cwd, "config", "--get", f"autopilot.{key}")
    return v if v else default


def cfg_bool(cwd, key, default):
    v = cfg(cwd, key, "").strip().lower()
    if v in ("true", "1", "yes", "on"):
        return True
    if v in ("false", "0", "no", "off"):
        return False
    return default


def protected(cwd):
    v = cfg(cwd, "protected", "")
    names = [s for s in re.split(r"[,\s]+", v) if s] if v else DEFAULT_PROTECTED
    return names


def base_branch(cwd):
    v = cfg(cwd, "base", "")
    if v:
        return v
    for b in protected(cwd):
        if git(cwd, "rev-parse", "--verify", "-q", f"refs/heads/{b}"):
            return b
    return protected(cwd)[0]


def branch_prefixes(cwd):
    return cfg(cwd, "branch-prefixes", DEFAULT_PREFIXES).split()


def branch_pattern(cwd):
    v = cfg(cwd, "branch-pattern", "")
    if v:
        return v
    return r"^(%s)/[a-z0-9][a-z0-9._-]*$" % "|".join(re.escape(p) for p in branch_prefixes(cwd))


def branch_hint(cwd):
    """给 agent 看的一行示例。"""
    return "git switch -c <%s>/<简短说明，小写，连字符>" % "|".join(branch_prefixes(cwd))
