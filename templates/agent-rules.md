<!-- git-autopilot:begin -->
## Git 约定（由 git-autopilot 维护，hook 会强制执行，别删这段）

- **不在 main / master 上改文件、提交、合并。** 动手前先开分支：`git switch -c <type>/<简短说明>`，
  `type` 取 `feat` `fix` `chore` `docs` `refactor` `test`，说明用小写英文和连字符，例如 `feat/login-form`。
  不合规的分支名会被拦。未提交的改动会跟着带到新分支，不会丢。
- **小步提交。** 每完成一个能用一句话说清的改动就 `git commit`，提交信息写"为什么"，不写"改了什么"。
  收工时若还有未提交改动，hook 会自动存档成 `wip:` 提交并推到远端同名分支；合并前把 wip 提交整理（squash）干净，
  整理后用 `git push --force-with-lease` 推自己的分支。
- **阶段性完成时，自己判断要不要提 PR。** 功能能跑、测试过、改动能用一段话说清，就算到了阶段。
  到了就先跑 code-review，然后**向人提议**："建议提交 PR 了"，附分支名、提交数、改动摘要。
  人同意再 `gh pr create`；没同意就继续干。合并也一样先提议，人说合再 `gh pr merge`，
  Claude Code 里执行时会再弹一次确认框给人，人点了才真的合；Codex / Pi 里这条命令会被直接拦下，
  由人自己在终端执行。别想办法绕开它。
- **不强推 main / master。** 任何分支上都会被拦。要覆盖远端，让人自己来。
- **确实必须在 main 上操作：** `touch .git/ALLOW_MAIN`，做完 `rm` 掉。
<!-- git-autopilot:end -->
