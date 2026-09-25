"""
wrap-up.py 自动推送的回归测试（issue #4）：只写源分支的 push 会继承 remote.<name>.push 的目标和强制语义。

只用临时目录里的本地 bare remote，不碰网络。
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
ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
       "GIT_ALLOW_PROTOCOL": "file", "PYTHONDONTWRITEBYTECODE": "1"}
for k in list(ENV):
    if k == "ALLOW_MAIN":
        del ENV[k]


def git(cwd, *args, check=True):
    r = subprocess.run(["git", "-C", str(cwd), *args], env=ENV, text=True, capture_output=True)
    if check and r.returncode:
        raise RuntimeError(r.stderr)
    return r.stdout.strip()


def run_hook(name, cwd):
    r = subprocess.run([sys.executable, str(HOOKS / name)], input=json.dumps({"cwd": str(cwd)}),
                       env=ENV, text=True, capture_output=True)
    return r.returncode, (json.loads(r.stdout).get("systemMessage", "") if r.stdout.strip() else ""), r.stderr


class AutopushRefspec(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.bare = root / "origin.git"
        self.bare.mkdir()
        git(self.bare, "init", "-q", "--bare")
        self.repo = root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "t")
        git(self.repo, "config", "user.email", "t@example.invalid")
        git(self.repo, "config", "core.hooksPath", os.devnull)
        git(self.repo, "config", "autopilot.pr-nudge-after", "99999")
        git(self.repo, "remote", "add", "origin", str(self.bare))
        (self.repo / "a").write_text("a\n")
        git(self.repo, "add", "a")
        git(self.repo, "commit", "-qm", "init")
        git(self.repo, "push", "-q", "origin", "main")
        # 远端 main 上再放一个本地没有的提交，让 feature 和 main 分叉
        (self.repo / "o").write_text("o\n")
        git(self.repo, "add", "o")
        git(self.repo, "commit", "-qm", "other")
        git(self.repo, "push", "-q", "origin", "main")
        self.main_before = git(self.bare, "rev-parse", "main")
        git(self.repo, "reset", "-q", "--hard", "HEAD~1")
        git(self.repo, "switch", "-qc", "feat/x")
        (self.repo / "f").write_text("f\n")
        git(self.repo, "add", "f")
        git(self.repo, "commit", "-qm", "feature")

    def tearDown(self):
        self.tmp.cleanup()

    def test_forced_push_mapping_in_config_does_not_touch_main(self):
        git(self.repo, "config", "remote.origin.push", "+refs/heads/feat/x:refs/heads/main")
        rc, msg, err = run_hook("wrap-up.py", self.repo)
        self.assertEqual(rc, 0, err)
        self.assertEqual(git(self.bare, "rev-parse", "main"), self.main_before, "远端 main 被改写了")
        self.assertEqual(git(self.bare, "rev-parse", "feat/x"), git(self.repo, "rev-parse", "HEAD"))
        self.assertEqual(git(self.repo, "config", "branch.feat/x.merge"), "refs/heads/feat/x")
        self.assertIn("已推到 origin/feat/x", msg)

    def test_plain_mapping_in_config_does_not_redirect(self):
        git(self.repo, "config", "remote.origin.push", "refs/heads/feat/x:refs/heads/main")
        rc, msg, err = run_hook("wrap-up.py", self.repo)
        self.assertEqual(rc, 0, err)
        self.assertEqual(git(self.bare, "rev-parse", "main"), self.main_before)
        self.assertEqual(git(self.bare, "rev-parse", "feat/x"), git(self.repo, "rev-parse", "HEAD"))

    def test_normal_push_still_works(self):
        rc, msg, err = run_hook("wrap-up.py", self.repo)
        self.assertEqual(rc, 0, err)
        self.assertEqual(git(self.bare, "rev-parse", "feat/x"), git(self.repo, "rev-parse", "HEAD"))
        self.assertIn("已推到 origin/feat/x", msg)


if __name__ == "__main__":
    unittest.main()
