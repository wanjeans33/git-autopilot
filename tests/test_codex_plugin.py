"""Exercise the packaged entry points after moving the bundle away from source."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CodexPlugin(unittest.TestCase):
    def test_relocated_bundle_guards_and_checkpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = root / "build/git-autopilot"
            subprocess.run([sys.executable, str(ROOT / "build_codex_plugin.py"), str(plugin)], check=True, capture_output=True)
            moved = root / "path with spaces/git-autopilot"
            moved.parent.mkdir()
            shutil.move(str(plugin), moved)
            config = json.loads((moved / "hooks/hooks.json").read_text())
            self.assertFalse((moved / ".claude-plugin").exists())
            env = dict(os.environ, PLUGIN_ROOT=str(moved), GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_CONFIG_NOSYSTEM="1", PYTHONDONTWRITEBYTECODE="1")
            env.pop("ALLOW_MAIN", None)
            repo = root / "repo"
            repo.mkdir()
            def git(*args):
                return subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True).stdout.strip()
            git("init", "-q", "-b", "main")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.invalid")
            git("config", "autopilot.gh-timeout", "0")
            remote = root / "remote.git"
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], env=env, check=True)
            git("remote", "add", "origin", str(remote))
            git("commit", "--allow-empty", "-qm", "initial")
            def hook(event, tool="Bash", command="git status"):
                declaration = config["hooks"][event][0]["hooks"][0]
                cmd = declaration["commandWindows"] if os.name == "nt" else declaration["command"]
                if os.name == "nt":
                    cmd = cmd.replace("${PLUGIN_ROOT}", str(moved))
                result = subprocess.run(cmd, shell=True, cwd=repo, env=env, text=True, capture_output=True,
                                        input=json.dumps({"cwd":str(repo), "tool_name":tool, "tool_input":{"command":command}}))
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout) if result.stdout.strip() else {}
            self.assertEqual(hook("PreToolUse", "apply_patch")["hookSpecificOutput"]["permissionDecision"], "deny")
            git("switch", "-qc", "feat/test")
            self.assertEqual(hook("PreToolUse", "apply_patch"), {})
            self.assertEqual(hook("PreToolUse", command="gh pr merge 12")["hookSpecificOutput"]["permissionDecision"], "deny")
            (repo / "work.txt").write_text("checkpoint me\n")
            hook("Stop")
            self.assertTrue(git("log", "-1", "--format=%s").startswith("wip:"))
            self.assertEqual(git("status", "--porcelain"), "")
            remote_head = subprocess.run(["git", "--git-dir", str(remote), "rev-parse", "refs/heads/feat/test"],
                                         env=env, check=True, capture_output=True, text=True).stdout.strip()
            self.assertEqual(remote_head, git("rev-parse", "HEAD"))

    def test_targeted_uninstall_preserves_other_hosts(self):
        spec = importlib.util.spec_from_file_location("installer", ROOT / "install.py")
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".git").mkdir()
            installer.main([str(repo)])
            claude = (repo / ".claude/settings.json").read_bytes()
            pi = (repo / ".pi/settings.json").read_bytes()
            agents = (repo / "AGENTS.md").read_bytes()
            installer.main(["--uninstall", "--agent=codex", str(repo)])
            self.assertFalse((repo / ".codex/hooks.json").exists())
            self.assertEqual((repo / ".claude/settings.json").read_bytes(), claude)
            self.assertEqual((repo / ".pi/settings.json").read_bytes(), pi)
            self.assertEqual((repo / "AGENTS.md").read_bytes(), agents)
