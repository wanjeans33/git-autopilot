"""
branch-status.py 的解析逻辑和端到端采集（issue #9）：领先 / 落后、已合并判断、wip 计数、
远端同步状态、仅远端分支、worktree 标记，以及 wrap-up 里的精简总览。

    python3 -m unittest discover -s tests
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

HOOKS = pathlib.Path(__file__).resolve().parent.parent / "hooks"
ENV = {k: v for k, v in os.environ.items() if k != "ALLOW_MAIN"}
ENV.update({
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "PYTHONDONTWRITEBYTECODE": "1",
    "PATH": os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep) if "gh" not in p.lower()),
})


def load():
    spec = importlib.util.spec_from_file_location("branch_status", HOOKS / "branch-status.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bs = load()


def git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), *args], env=ENV, text=True, capture_output=True)
    if r.returncode:
        raise RuntimeError(r.stderr)
    return r.stdout.strip()


def commit(cwd, msg):
    git(cwd, "commit", "-q", "--allow-empty", "-m", msg)


class Parsing(unittest.TestCase):
    def test_track(self):
        self.assertEqual(bs.parse_track("", ""), "none")
        self.assertEqual(bs.parse_track("origin/x", ""), "same")
        self.assertEqual(bs.parse_track("origin/x", "[ahead 2]"), "ahead")
        self.assertEqual(bs.parse_track("origin/x", "[behind 1]"), "behind")
        self.assertEqual(bs.parse_track("origin/x", "[ahead 2, behind 1]"), "diverged")
        self.assertEqual(bs.parse_track("origin/x", "[gone]"), "gone")

    def test_ahead_behind(self):
        self.assertEqual(bs.parse_ahead_behind("3\t2"), (2, 3))
        self.assertEqual(bs.parse_ahead_behind(""), (None, None))

    def test_refs_skip_symref_and_keep_last_line(self):
        sep = bs.SEP
        text = sep.join(["origin", " ", "", "", "1", "x", "refs/remotes/origin/main", "commit"]) + "\n" \
            + sep.join(["main", "*", "origin/main", "", "1700000000", "init", "", "commit"])
        refs = bs.parse_refs(text)
        self.assertEqual([r["name"] for r in refs], ["main"])
        self.assertTrue(refs[0]["current"])
        self.assertEqual(refs[0]["track"], "same")

    def test_ci_summary(self):
        self.assertEqual(bs.summarize_ci([]), "none")
        self.assertEqual(bs.summarize_ci([{"conclusion": "SUCCESS"}, {"state": "SUCCESS"}]), "ok")
        self.assertEqual(bs.summarize_ci([{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}]), "fail")
        self.assertEqual(bs.summarize_ci([{"conclusion": "SUCCESS"}, {"status": "IN_PROGRESS", "conclusion": ""}]), "pending")
        self.assertEqual(bs.summarize_ci([{"state": "FAILURE"}, {"conclusion": ""}]), "fail")

    def test_pick_pr_prefers_open_then_newest(self):
        prs = [
            {"number": 1, "state": "CLOSED", "headRefName": "feat/x"},
            {"number": 4, "state": "MERGED", "headRefName": "feat/x"},
            {"number": 2, "state": "OPEN", "headRefName": "feat/y", "reviewDecision": "APPROVED"},
        ]
        self.assertEqual(bs.pick_pr(prs, "feat/x")["number"], 4)
        self.assertEqual(bs.pick_pr(prs, "feat/y")["review"], "approved")
        self.assertIsNone(bs.pick_pr(prs, "feat/z"))

    def test_worktrees_exclude_here(self):
        text = "worktree /a\nHEAD 1\nbranch refs/heads/main\n\nworktree /b\nHEAD 2\nbranch refs/heads/feat/x\n\nworktree /c\nHEAD 3\ndetached\n"
        self.assertEqual(bs.parse_worktrees(text, "/a"), {"feat/x": "/b"})


class Collect(unittest.TestCase):
    """真仓库：origin 是 bare 仓，覆盖已合并 / 领先落后 / wip / gone / 仅远端 / worktree 几种情形。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.origin = root / "origin.git"
        git(root, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.repo = root / "repo"
        git(root, "init", "-q", "-b", "main", str(self.repo))
        r = self.repo
        git(r, "config", "user.name", "t")
        git(r, "config", "user.email", "t@example.invalid")
        git(r, "remote", "add", "origin", str(self.origin))
        commit(r, "init")
        git(r, "push", "-q", "-u", "origin", "main")

        # feat/merged：从 main 分出后 main 又前进 → ahead 0 behind 1
        git(r, "branch", "feat/merged")
        commit(r, "main moves on")
        git(r, "push", "-q", "origin", "main")

        # feat/ahead：两个提交（一个 wip），推过一次后再加一个 → 相对 main +3，本地领先远端
        git(r, "switch", "-qc", "feat/ahead")
        commit(r, "work")
        commit(r, "wip: 收工自动存档")
        git(r, "push", "-q", "-u", "origin", "feat/ahead")
        commit(r, "more")

        # feat/gone：推过，远端又删了
        git(r, "switch", "-qc", "feat/gone", "main")
        commit(r, "gone work")
        git(r, "push", "-q", "-u", "origin", "feat/gone")
        git(r, "push", "-q", "origin", "--delete", "feat/gone")
        git(r, "fetch", "-q", "--prune")

        # feat/remote-only：只在远端有
        git(r, "push", "-q", "origin", "main:refs/heads/feat/remote-only")
        git(r, "fetch", "-q")

        # feat/wt：在另一个 worktree 里签出
        self.wt = root / "wt"
        git(r, "worktree", "add", "-q", "-b", "feat/wt", str(self.wt), "main")

        git(r, "switch", "-q", "feat/ahead")
        (r / "dirty.txt").write_text("x")

    def tearDown(self):
        self.tmp.cleanup()

    def run_status(self, *argv):
        r = subprocess.run([sys.executable, str(HOOKS / "branch-status.py"), "--json", "--no-gh", *argv],
                           cwd=str(self.repo), env=ENV, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        return data["meta"], {b["name"]: b for b in data["branches"]}

    def test_collect(self):
        meta, b = self.run_status()
        self.assertEqual(meta["base"], "main")
        self.assertFalse(meta["gh"])
        self.assertEqual(set(b), {"main", "feat/merged", "feat/ahead", "feat/gone", "feat/wt", "origin/feat/remote-only"})

        self.assertTrue(b["main"]["base"])
        self.assertEqual(b["main"]["track"], "same")

        m = b["feat/merged"]
        self.assertEqual((m["ahead"], m["behind"]), (0, 1))
        self.assertTrue(m["merged"])
        self.assertEqual(m["track"], "none")
        self.assertIn("已合并，可删", bs.notes(m, "main"))
        self.assertNotIn("未推送", bs.notes(m, "main"))   # 已合并的不用催推

        a = b["feat/ahead"]
        self.assertTrue(a["current"])
        self.assertEqual((a["ahead"], a["behind"]), (3, 0))
        self.assertFalse(a["merged"])
        self.assertEqual(a["wip"], 1)
        self.assertEqual(a["track"], "ahead")
        self.assertTrue(a["dirty"])
        self.assertEqual(bs.notes(a, "main"), ["wip 提交 1 个", "未提交改动", "本地领先远端"])

        g = b["feat/gone"]
        self.assertEqual(g["track"], "gone")
        self.assertIn("远端分支已删", bs.notes(g, "main"))

        ro = b["origin/feat/remote-only"]
        self.assertEqual(ro["kind"], "remote")
        self.assertEqual(ro["short"], "feat/remote-only")
        self.assertEqual((ro["ahead"], ro["behind"]), (0, 0))

        w = b["feat/wt"]
        self.assertEqual(os.path.realpath(w["worktree"]), os.path.realpath(str(self.wt)))
        self.assertIn("在 worktree wt", bs.notes(w, "main"))

    def test_brief_lists_only_attention(self):
        r = subprocess.run([sys.executable, str(HOOKS / "branch-status.py"), "--brief"],
                           cwd=str(self.repo), env=ENV, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        names = [l.split("：")[0] for l in lines]
        self.assertIn("feat/merged", names)
        self.assertIn("feat/ahead", names)
        self.assertIn("feat/gone", names)
        self.assertNotIn("main", names)
        self.assertNotIn("origin/feat/remote-only", names)   # 和 main 相同，没什么可说

    def test_table_renders_in_worktree(self):
        r = subprocess.run([sys.executable, str(HOOKS / "branch-status.py")],
                           cwd=str(self.wt), env=ENV, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("* feat/wt", r.stdout)
        self.assertIn("在 worktree repo", r.stdout)
        # tempfile 在 macOS 上是 /var → /private/var 的符号链接：当前 worktree 自己不能被当成"别的 worktree"
        self.assertNotIn("在 worktree wt", r.stdout)   # 从 worktree 看，主目录里签出的 feat/ahead 是"别的 worktree"

    def test_wrap_up_appends_overview(self):
        git(self.repo, "config", "autopilot.autocommit", "false")
        git(self.repo, "config", "autopilot.autopush", "false")
        ev = json.dumps({"cwd": str(self.repo)})
        r = subprocess.run([sys.executable, str(HOOKS / "wrap-up.py")], input=ev,
                           cwd=str(self.repo), env=ENV, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        msg = json.loads(r.stdout)["systemMessage"]
        self.assertIn("其他分支：", msg)
        self.assertIn("feat/merged：已合并，可删", msg)
        self.assertNotIn("feat/ahead：", msg)   # 当前分支的事上面已经说过

        git(self.repo, "config", "autopilot.branch-overview-after", "99")
        r = subprocess.run([sys.executable, str(HOOKS / "wrap-up.py")], input=ev,
                           cwd=str(self.repo), env=ENV, text=True, capture_output=True)
        self.assertNotIn("其他分支", r.stdout)


if __name__ == "__main__":
    unittest.main()
