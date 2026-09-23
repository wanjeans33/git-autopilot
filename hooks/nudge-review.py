#!/usr/bin/env python3
"""
nudge-review.py —— Stop 阶段提示：这条分支攒了多少提交，要不要审一下再合。
三家同样共用：Stop 事件 stdin 给 JSON，stdout 回 {"systemMessage": "..."}。
不拦任何东西，只是给人看一句话。
"""
import json
import os
import subprocess
import sys

BASE = "main"


def git(cwd, *a):
    try:
        r = subprocess.run(["git", "-C", cwd, *a], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


try:
    ev = json.load(sys.stdin)
except Exception:
    ev = {}
cwd = ev.get("cwd") or os.getcwd()

branch = git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
if not branch or branch in ("main", "master", "HEAD"):
    sys.exit(0)

ahead = git(cwd, "rev-list", "--count", f"{BASE}..HEAD")
dirty = git(cwd, "status", "--porcelain")
if not ahead.isdigit() or int(ahead) == 0:
    sys.exit(0)

files = git(cwd, "diff", "--name-only", f"{BASE}...HEAD")
n_files = len([f for f in files.splitlines() if f])
msg = f"分支 {branch} 已比 {BASE} 多 {ahead} 个提交、{n_files} 个文件。"
if dirty:
    msg += " 还有未提交改动。"
msg += " 合并前建议先跑 /code-review。"

json.dump({"systemMessage": msg}, sys.stdout)
