"""
issue #6 的三个回归：未出生分支上 main 不设防、子目录里 .git/ALLOW_MAIN 失效、git add 失败被吞。

    python3 -m unittest discover -s tests
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

HOOKS = pathlib.Path(__file__).resolve().parent.parent / "hooks"
# 去掉宿主环境里的 GIT_DIR / GIT_WORK_TREE 之类（从 git hook 里跑测试时会带着），再隔离全局配置
ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k != "ALLOW_MAIN"}
ENV.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_ALLOW_PROTOCOL": "file", "PYTHONDONTWRITEBYTECODE": "1"})


def git_version():
    out = subprocess.run(["git", "--version"], text=True, capture_output=True).stdout
    return tuple(int(x) for x in out.split()[2].split(".")[:2]) if out.startswith("git version") else (0, 0)


# GIT_CONFIG_GLOBAL 是 2.32 才有的，更老的 git 会静默忽略，用户的全局配置就漏进来了
REQUIRE_GIT = (2, 32)


def git(cwd, *args, check=True):
    r = subprocess.run(["git", "-C", str(cwd), *args], env=ENV, text=True, capture_output=True)
    if check and r.returncode:
        raise RuntimeError(r.stderr)
    return r.stdout.strip()


def guard(cwd, tool="Edit", command=None):
    ev = {"cwd": str(cwd), "tool_name": tool, "tool_input": {"command": command} if command else {}}
    r = subprocess.run([sys.executable, str(HOOKS / "guard-branch.py")],
                       input=json.dumps(ev), env=ENV, text=True, capture_output=True)
    if r.returncode or r.stderr:
        raise RuntimeError(r.stderr)
    return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] if r.stdout.strip() else "allow"


def wrap_up(cwd):
    r = subprocess.run([sys.executable, str(HOOKS / "wrap-up.py")], input=json.dumps({"cwd": str(cwd)}),
                       env=ENV, text=True, capture_output=True)
    if r.returncode or r.stderr:
        raise RuntimeError(r.stderr)
    return json.loads(r.stdout).get("systemMessage", "") if r.stdout.strip() else ""


@unittest.skipUnless(git_version() >= REQUIRE_GIT, f"需要 git >= {'.'.join(map(str, REQUIRE_GIT))}")
class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "t")
        git(self.repo, "config", "user.email", "t@example.invalid")
        git(self.repo, "config", "core.hooksPath", os.devnull)
        git(self.repo, "config", "autopilot.autopush", "false")
        git(self.repo, "config", "autopilot.pr-nudge-after", "99999")

    def tearDown(self):
        self.tmp.cleanup()

    def first_commit(self):
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "init")


class UnbornBranch(Base):
    def test_unborn_main_denies_edit_and_commit(self):
        self.assertEqual(guard(self.repo, "Edit"), "deny")
        self.assertEqual(guard(self.repo, "Bash", "git commit --allow-empty -m init"), "deny")

    def test_unborn_feature_branch_allows_and_wrap_up_creates_root_commit(self):
        git(self.repo, "switch", "-qc", "feat/x")
        self.assertEqual(guard(self.repo, "Edit"), "allow")
        (self.repo / "a").write_text("a\n")
        msg = wrap_up(self.repo)
        self.assertIn("已自动存档 1 个文件", msg)
        self.assertEqual(git(self.repo, "log", "--format=%s", "-1"), "wip: 收工自动存档 — a")

    def test_unborn_feature_branch_with_remote_and_nothing_to_commit_stays_quiet(self):
        # clone 了空仓库、开了分支、什么都没提交：没有 HEAD，不能去推，也不能报"推送失败"
        bare = pathlib.Path(self.tmp.name) / "origin.git"
        bare.mkdir()
        git(bare, "init", "-q", "--bare")
        git(self.repo, "remote", "add", "origin", str(bare))
        git(self.repo, "config", "--unset", "autopilot.autopush")
        git(self.repo, "switch", "-qc", "feat/x")
        self.assertEqual(wrap_up(self.repo), "")

    def test_tag_named_main_does_not_unlock_main(self):
        self.first_commit()
        git(self.repo, "tag", "main")
        self.assertEqual(guard(self.repo, "Edit"), "deny")
        self.assertEqual(guard(self.repo, "Bash", "git commit --allow-empty -m x"), "deny")

    def test_detached_head_is_left_alone(self):
        self.first_commit()
        git(self.repo, "switch", "-q", "--detach")
        self.assertEqual(guard(self.repo, "Edit"), "allow")
        (self.repo / "a").write_text("a\n")
        self.assertEqual(wrap_up(self.repo), "")
        self.assertEqual(git(self.repo, "status", "--porcelain"), "?? a")


class AllowMainMarker(Base):
    def test_marker_works_from_subdirectory(self):
        self.first_commit()
        (self.repo / "src").mkdir()
        self.assertEqual(guard(self.repo / "src", "Edit"), "deny")
        (self.repo / ".git" / "ALLOW_MAIN").touch()
        self.assertEqual(guard(self.repo, "Edit"), "allow")
        self.assertEqual(guard(self.repo / "src", "Edit"), "allow")

    def test_marker_works_from_linked_worktree(self):
        self.first_commit()
        wt = pathlib.Path(self.tmp.name) / "wt"
        git(self.repo, "worktree", "add", "-q", str(wt), "-b", "feat/wt")
        git(wt, "switch", "-qc", "master")  # 在 worktree 里也站到一个保护分支上
        self.assertEqual(guard(wt, "Edit"), "deny")
        (self.repo / ".git" / "ALLOW_MAIN").touch()
        self.assertEqual(guard(wt, "Edit"), "allow")
        (wt / "sub").mkdir()
        self.assertEqual(guard(wt / "sub", "Edit"), "allow")


class AddFailure(Base):
    def test_add_failure_aborts_commit_and_reports(self):
        (self.repo / "base.txt").write_text("base\n")
        git(self.repo, "add", "base.txt")
        self.first_commit()
        git(self.repo, "switch", "-qc", "feat/x")
        (self.repo / "base.txt").write_text("staged change\n")
        git(self.repo, "add", "base.txt")
        nested = self.repo / "nested"
        nested.mkdir()
        git(nested, "init", "-q", "-b", "main")   # 没有提交的嵌套仓库让 git add -A 失败
        before = git(self.repo, "rev-parse", "HEAD")
        msg = wrap_up(self.repo)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), before, "add 失败后不该产生提交")
        self.assertIn("自动存档失败", msg)
        self.assertIn("git add", msg)
        self.assertIn("nested", msg, "提示里应带上真正的原因（error: 那行），不是笼统的 fatal")
        self.assertNotIn("已自动存档", msg)

    def test_count_is_right_with_a_file_named_HEAD_and_root_commit(self):
        git(self.repo, "switch", "-qc", "feat/x")          # 未出生分支，这次存档就是根提交
        git(self.repo, "config", "log.showRoot", "false")  # git show 在这个配置下根提交不列文件
        (self.repo / "HEAD").write_text("h\n")             # git show HEAD 会报"既是引用又是路径"
        (self.repo / "a").write_text("a\n")
        msg = wrap_up(self.repo)
        self.assertIn("已自动存档 2 个文件", msg)

    def test_reported_count_matches_commit(self):
        self.first_commit()
        git(self.repo, "switch", "-qc", "feat/x")
        (self.repo / "a").write_text("a\n")
        (self.repo / "b").write_text("b\n")
        (self.repo / ".gitignore").write_text("b\n")   # b 被忽略，status 里不会出现
        msg = wrap_up(self.repo)
        self.assertIn("已自动存档 2 个文件", msg)  # a 和 .gitignore
        self.assertEqual(sorted(git(self.repo, "show", "--format=", "--name-only", "HEAD").split()), [".gitignore", "a"])


if __name__ == "__main__":
    unittest.main()
