"""
guard-branch.py 对 gh pr merge 的宿主区分（issue #5）：只有 Claude Code 会把 ask 变成确认框，
Codex 文档写明 ask 不支持且会继续执行工具，所以其他宿主一律 deny。

    python3 -m unittest discover -s tests
"""
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


def git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), *args], env=ENV, text=True, capture_output=True)
    if r.returncode:
        raise RuntimeError(r.stderr)
    return r.stdout.strip()


def guard(cwd, command, *argv):
    ev = {"cwd": str(cwd), "tool_name": "Bash", "tool_input": {"command": command}}
    r = subprocess.run([sys.executable, str(HOOKS / "guard-branch.py"), *argv],
                       input=json.dumps(ev), env=ENV, text=True, capture_output=True)
    if r.returncode or r.stderr:
        raise RuntimeError(r.stderr)
    return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] if r.stdout.strip() else "allow"


class MergeConfirmationPerHost(unittest.TestCase):
    MERGE = "gh pr merge 12 --squash"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.tmp.name)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "t")
        git(self.repo, "config", "user.email", "t@example.invalid")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "init")
        git(self.repo, "switch", "-qc", "feat/x")

    def tearDown(self):
        self.tmp.cleanup()

    def test_claude_gets_ask(self):
        self.assertEqual(guard(self.repo, self.MERGE, "--host", "claude"), "ask")
        self.assertEqual(guard(self.repo, self.MERGE, "--host=claude"), "ask")

    def test_codex_and_pi_get_deny(self):
        self.assertEqual(guard(self.repo, self.MERGE, "--host", "codex"), "deny")
        self.assertEqual(guard(self.repo, self.MERGE, "--host", "pi"), "deny")

    def test_unknown_host_gets_deny(self):
        self.assertEqual(guard(self.repo, self.MERGE), "deny")

    def test_host_flag_does_not_affect_other_rules(self):
        self.assertEqual(guard(self.repo, "git status", "--host", "codex"), "allow")
        self.assertEqual(guard(self.repo, "git push --force origin main", "--host", "codex"), "deny")

    @unittest.skipUnless(shutil.which("sh") and os.name != "nt", "插件启动器需要 POSIX sh；Windows 路径没有 / 分隔，py.sh 找不到脚本")
    def test_plugin_launcher_forwards_host(self):
        ev = {"cwd": str(self.repo), "tool_name": "Bash", "tool_input": {"command": self.MERGE}}
        r = subprocess.run(["sh", str(HOOKS / "py.sh"), "guard-branch.py", "--host", "claude"],
                           input=json.dumps(ev), env=ENV, text=True, capture_output=True)
        self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"], "ask", r.stderr)

    def test_installed_templates_declare_host(self):
        for name, host in (("claude.settings.json", "claude"), ("codex.hooks.json", "codex"), ("pi.settings.json", "pi")):
            cfg = json.loads((HOOKS.parent / "templates" / name).read_text(encoding="utf-8"))
            cmd = cfg["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
            self.assertIn(f"--host {host}", cmd, name)
        plugin = json.loads((HOOKS / "hooks.json").read_text(encoding="utf-8"))
        self.assertIn("--host claude", plugin["hooks"]["PreToolUse"][0]["hooks"][0]["command"])


if __name__ == "__main__":
    unittest.main()
