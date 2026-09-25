---
name: git-autopilot
description: 本机装了 git-autopilot 的 git 约定：不在 main/master 上干活、分支命名、小步提交、收工自动存档推送、阶段性提议 PR、合并前弹框确认。在任何 git 仓库里改文件、开分支、提交、开或合 PR 之前加载；被守卫拦下时也加载，里面有补救办法。
---

# git-autopilot 约定

这台机器装了 git-autopilot 插件，下面的规则有 hook 强制执行，不是建议。

- **不在 main / master 上改文件、提交、合并。** 动手前先开分支：`git switch -c <type>/<简短说明>`，
  `type` 取 `feat` `fix` `chore` `docs` `refactor` `test`，说明用小写英文和连字符，例如 `feat/login-form`。
  不合规的分支名会被拦。未提交的改动会跟着带到新分支，不会丢。
- **小步提交。** 每完成一个能用一句话说清的改动就 `git commit`，提交信息写"为什么"，不写"改了什么"。
  收工时若还有未提交改动，hook 会自动存档成 `wip:` 提交并推到远端同名分支；合并前把 wip 提交整理（squash）干净，
  整理后用 `git push --force-with-lease` 推自己的分支。
- **阶段性完成时，自己判断要不要提 PR。** 功能能跑、测试过、改动能用一段话说清，就算到了阶段。
  到了就先跑 code-review，然后**向人提议**："建议提交 PR 了"，附分支名、提交数、改动摘要。
  人同意再 `gh pr create`；没同意就继续干。合并也一样先提议，人说合再 `gh pr merge`，
  执行时还会再弹一次确认框给人，人点了才真的合。别想办法绕开它。
- **不强推 main / master。** 任何分支上都会被拦。要覆盖远端，让人自己来。
- **确实必须在 main 上操作：** `touch .git/ALLOW_MAIN`，做完 `rm` 掉。
- **想看各分支状态**（领先 / 落后、有没有 PR、能不能删、有没有 wip）：
  `python3 "${CLAUDE_PLUGIN_ROOT}/hooks/branch-status.py"`，只读，`--fetch` 才联网。
- **这个仓库不想要这套：** `git config autopilot.enabled false`。

## 被拦了怎么办

守卫的拒绝理由里就写着补救命令，照做即可。最常见的是在 main 上被拦：`git switch -c feat/xxx` 后重试原操作。
分支名被拦：换成合规的名字。命令因为字面含有 `git commit`、`git push --force` 之类字样被误拦：换个措辞，
或把内容写进文件再执行。
