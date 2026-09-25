#!/usr/bin/env python3
"""Build a self-contained Codex plugin (Python 3.8+, no Codex SDK required)."""
import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def build(output):
    output = Path(output).expanduser().resolve()
    if output.name != "git-autopilot":
        raise ValueError("Output directory must be named git-autopilot")
    if output == ROOT or ROOT in output.parents and output != ROOT / "dist" / "git-autopilot":
        raise ValueError("Use dist/git-autopilot or a directory outside the source repository")
    if output.exists():
        raise ValueError("Output already exists; use a fresh directory to avoid overwriting files")
    output.mkdir(parents=True)
    (output / ".codex-plugin").mkdir()
    shutil.copy2(ROOT / "templates/codex-plugin.json", output / ".codex-plugin/plugin.json")
    shutil.copytree(ROOT / "skills", output / "skills", ignore=shutil.ignore_patterns("__pycache__"))
    (output / "hooks").mkdir()
    for name in ("guard-branch.py", "wrap-up.py", "_common.py", "py.sh"):
        shutil.copy2(ROOT / "hooks" / name, output / "hooks" / name)
    config = json.loads((ROOT / "templates/codex.hooks.json").read_text())
    for event, groups in config["hooks"].items():
        for group in groups:
            for hook in group["hooks"]:
                script = "guard-branch.py --host codex" if event == "PreToolUse" else "wrap-up.py"
                hook["command"] = 'sh "${PLUGIN_ROOT}/hooks/py.sh" ' + script
                hook["commandWindows"] = 'python "${PLUGIN_ROOT}/hooks/' + script.split()[0] + '"' + (" --host codex" if event == "PreToolUse" else "")
                hook.setdefault("timeout", 10)
    (output / "hooks/hooks.json").write_text(json.dumps(config, indent=2) + "\n")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", default=str(ROOT / "dist/git-autopilot"))
    args = parser.parse_args()
    try:
        print(build(args.output))
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
