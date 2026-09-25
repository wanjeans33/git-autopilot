"""
guard-branch.py 合并 PR 的确认框要带够信息（issue #8）：弹框前用 gh pr view 查 PR 摘要拼进 reason；
查不到、超时都要明说"没查到"，不能静默退回只有一行命令。

用一个假 gh 顶在 PATH 前面，按 FAKE_GH_MODE 决定返回：ok / fail / slow。

    python3 -m unittest discover -s tests
"""
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

HOOKS = pathlib.Path(__file__).resolve().parent.parent / "hooks"
ENV = {k: v for k, v in os.environ.items() if k != "ALLOW_MAIN"}
ENV.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "PYTHONDONTWRITEBYTECODE": "1"})

FAKE_GH = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_GH_LOG"
case "$FAKE_GH_MODE" in
  ok)   cat "$FAKE_GH_JSON" ;;
  fail) echo "GraphQL: Could not resolve to a PullRequest with the number of 9999." >&2; exit 1 ;;
  slow) sleep 5; echo '{}' ;;
  junk) echo "Welcome to gh 3.0! See changelog."; cat "$FAKE_GH_JSON" ;;
esac
"""

PR = {
    "number": 12, "title": "让确认框带上 PR 信息", "url": "https://example.invalid/pull/12",
    "state": "OPEN", "isDraft": False, "headRefName": "feat/x", "baseRefName": "main",
    "commits": [{"oid": "a"}, {"oid": "b"}, {"oid": "c"}], "changedFiles": 4, "additions": 86, "deletions": 12,
    "statusCheckRollup": [{"conclusion": "SUCCESS", "status": "COMPLETED"}, {"state": "SUCCESS"}],
    "reviewDecision": "APPROVED", "mergeable": "MERGEABLE",
}


def git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), *args], env=ENV, text=True, capture_output=True)
    if r.returncode:
        raise RuntimeError(r.stderr)
    return r.stdout.strip()


def load_guard():
    """把 guard-branch.py 当模块导入，直接测里面的纯函数（文件名带连字符，不能普通 import）。"""
    if str(HOOKS) not in sys.path:
        sys.path.insert(0, str(HOOKS))
    spec = importlib.util.spec_from_file_location("guard_branch", HOOKS / "guard-branch.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class MergeArgsParsing(unittest.TestCase):
    def setUp(self):
        self.g = load_guard()

    def test_number_and_flags(self):
        a = self.g.merge_args("gh pr merge 12 --squash --delete-branch")
        self.assertEqual((a["selector"], a["method"], a["delete"], a["auto"]), ("12", "squash", True, False))

    def test_short_flags_and_repo(self):
        a = self.g.merge_args("cd x && gh pr merge -R o/r 7 -r -d --auto")
        self.assertEqual((a["selector"], a["repo"], a["method"], a["delete"], a["auto"]), ("7", "o/r", "rebase", True, True))
        self.assertEqual(self.g.merge_args("gh pr merge --repo=o/r")["repo"], "o/r")

    def test_no_selector_and_body_arg_skipped(self):
        a = self.g.merge_args("gh pr merge -m --body 'not a selector'")
        self.assertEqual((a["selector"], a["method"]), ("", "merge"))
        self.assertEqual(self.g.merge_args("gh pr merge https://x/pull/3 -s")["selector"], "https://x/pull/3")

    def test_ci_status(self):
        ci = self.g.ci_status
        self.assertEqual(ci([]), "没配")
        self.assertEqual(ci(None), "没配")
        self.assertIn("通过", ci([{"conclusion": "SUCCESS"}, {"state": "SUCCESS"}, {"conclusion": "SKIPPED"}]))
        self.assertIn("失败", ci([{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}]))
        self.assertIn("还在跑", ci([{"conclusion": "SUCCESS"}, {"conclusion": "", "status": "IN_PROGRESS"}]))
        self.assertIn("失败", ci([{"state": "PENDING"}, {"state": "FAILURE"}]))  # 有失败就算失败，不算还在跑


@unittest.skipUnless(shutil.which("sh") and os.name != "nt", "假 gh 是 POSIX sh 脚本")
class MergeConfirmationCarriesPrInfo(unittest.TestCase):
    MERGE = "gh pr merge 12 --squash --delete-branch"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.repo = root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "t")
        git(self.repo, "config", "user.email", "t@example.invalid")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "init")
        git(self.repo, "switch", "-qc", "feat/x")
        bin_dir = root / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(FAKE_GH, encoding="utf-8")
        gh.chmod(0o755)
        self.log = root / "gh.log"
        self.pr_json = root / "pr.json"
        self.pr_json.write_text(json.dumps(PR), encoding="utf-8")
        self.env = dict(ENV, PATH=f"{bin_dir}{os.pathsep}{ENV.get('PATH', '')}",
                        FAKE_GH_LOG=str(self.log), FAKE_GH_JSON=str(self.pr_json))

    def tearDown(self):
        self.tmp.cleanup()

    def guard(self, command, host, mode):
        ev = {"cwd": str(self.repo), "tool_name": "Bash", "tool_input": {"command": command}}
        r = subprocess.run([sys.executable, str(HOOKS / "guard-branch.py"), "--host", host],
                           input=json.dumps(ev), env=dict(self.env, FAKE_GH_MODE=mode), text=True, capture_output=True)
        if r.returncode or r.stderr:
            raise RuntimeError(r.stderr)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        return out["permissionDecision"], out["permissionDecisionReason"]

    def gh_calls(self):
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def test_ask_shows_enough_to_judge(self):
        decision, reason = self.guard(self.MERGE, "claude", "ok")
        self.assertEqual(decision, "ask")
        for piece in ("PR #12", "让确认框带上 PR 信息", "https://example.invalid/pull/12",
                      "feat/x → main（保护分支）", "3 个提交", "4 个文件", "+86 / -12",
                      "CI：通过", "review：已批准", "冲突：无", "draft：否",
                      "方式：squash", "删远端分支：是", "命令：" + self.MERGE, "确认要合"):
            self.assertIn(piece, reason)
        self.assertNotIn("没查到", reason)
        self.assertEqual(self.gh_calls(), [f"pr view 12 --json {load_guard().PR_FIELDS}"])

    def test_deny_path_carries_same_info(self):
        decision, reason = self.guard(self.MERGE, "codex", "ok")
        self.assertEqual(decision, "deny")
        self.assertIn("PR #12", reason)
        self.assertIn("CI：通过", reason)
        self.assertIn("交给人在终端自己执行", reason)

    def test_compound_command_does_not_query_wrong_repository(self):
        for host, expected in (("claude", "ask"), ("codex", "deny")):
            decision, reason = self.guard("cd /another/repo && " + self.MERGE, host, "ok")
            self.assertEqual(decision, expected)
            self.assertIn("仓库上下文无法可靠确定", reason)
            self.assertNotIn("CI：通过", reason)
        self.assertEqual(self.gh_calls(), [])

    def test_not_found_says_so_instead_of_pretending(self):
        decision, reason = self.guard("gh pr merge 9999 -m", "claude", "fail")
        self.assertEqual(decision, "ask")
        self.assertIn("没查到 PR 信息：GraphQL: Could not resolve", reason)
        self.assertIn("盲判", reason)
        self.assertIn("gh pr view 9999", reason)
        self.assertIn("方式：merge", reason)
        self.assertIn("确认要合", reason)

    def test_timeout_says_so(self):
        git(self.repo, "config", "autopilot.gh-timeout", "0.3")
        decision, reason = self.guard(self.MERGE, "claude", "slow")
        self.assertEqual(decision, "ask")
        self.assertIn("没查到 PR 信息", reason)
        self.assertIn("0.3 秒没返回", reason)

    def test_no_selector_and_repo_passthrough(self):
        self.guard("gh pr merge -R o/r --rebase", "claude", "ok")
        self.assertEqual(self.gh_calls()[-1], f"pr view -R o/r --json {load_guard().PR_FIELDS}")

    def test_missing_gh_binary(self):
        self.env["PATH"] = str(pathlib.Path(self.tmp.name) / "nowhere")
        # 没有 PATH 上的 gh，subprocess 抛 FileNotFoundError；仍要弹框且明说
        ev = {"cwd": str(self.repo), "tool_name": "Bash", "tool_input": {"command": self.MERGE}}
        r = subprocess.run([sys.executable, str(HOOKS / "guard-branch.py"), "--host", "claude"],
                           input=json.dumps(ev), env=dict(self.env, FAKE_GH_MODE="ok"), text=True, capture_output=True)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "ask")
        self.assertIn("跑不了 gh", out["permissionDecisionReason"])

    def test_non_json_output_still_asks(self):
        decision, reason = self.guard(self.MERGE, "claude", "junk")
        self.assertEqual(decision, "ask")
        self.assertIn("没查到 PR 信息：gh 返回的不是 JSON", reason)
        self.assertIn(self.MERGE, reason)

    def test_timeout_zero_skips_lookup(self):
        git(self.repo, "config", "autopilot.gh-timeout", "0")
        decision, reason = self.guard(self.MERGE, "claude", "ok")
        self.assertEqual(decision, "ask")
        self.assertIn("gh-timeout 设成了 0", reason)
        self.assertEqual(self.gh_calls(), [])  # 真的没去跑 gh

    def test_bad_timeout_value_falls_back_to_default(self):
        git(self.repo, "config", "autopilot.gh-timeout", "fast")
        decision, reason = self.guard(self.MERGE, "claude", "ok")
        self.assertEqual(decision, "ask")
        self.assertIn("PR #12", reason)

    def test_commit_count_cap_is_marked(self):
        self.pr_json.write_text(json.dumps(dict(PR, commits=[{"oid": str(i)} for i in range(100)])), encoding="utf-8")
        _, reason = self.guard(self.MERGE, "claude", "ok")
        self.assertIn("100+ 个提交", reason)

    def test_closed_pr_is_flagged(self):
        self.pr_json.write_text(json.dumps(dict(PR, state="MERGED", isDraft=True, mergeable="CONFLICTING")), encoding="utf-8")
        _, reason = self.guard(self.MERGE, "claude", "ok")
        self.assertIn("状态是 MERGED", reason)
        self.assertIn("draft：是", reason)
        self.assertIn("冲突：有冲突", reason)


if __name__ == "__main__":
    unittest.main()
