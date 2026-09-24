# git-autopilot

给 AI coding agent 装一个 **git 自动驾驶**。

装上之后，你只管用自然语言跟 agent 说要做什么。分支什么时候该开、叫什么名字、收工要不要存档、
合并前要不要先审，它替你把关。**你随时可以接管。**

给谁用：不想管 git、也没必要学 git 的人。装完就不用再操心了。

---

## 它替你做什么

- **不让在 main 上直接干活。** 你（或 agent）一动手改文件，它先拦下来，提醒开一条分支。
  未提交的改动会原样带到新分支，**不会丢**，接着做就行。
- **分支名要合规。** `feat/` `fix/` `chore/` `docs/` `refactor/` `test/` 打头，小写、连字符。
  不合规的 `git switch -c` 直接拦，并告诉 agent 该怎么起名。
- **收工自动存档、自动推送。** 每次 agent 停下来，工作分支上还有没提交的改动，就自动打成一个 `wip:` 提交，
  再推到远端同名分支。电脑死机、会话崩了，改动都在，GitHub 上也有。**不碰 main**，merge / rebase 进行中也不碰，
  推送永远不带 force。
- **收工提醒一句：** 这条分支已经攒了多少提交、多少文件，要不要先审一遍再合。
- **该提 PR 了会提议，合并前一定弹框问你。** 阶段性完成由 agent 判断，判断到了就向你提议"建议提交 PR"，
  你点头它才 `gh pr create`。合并可以由 agent 执行，但 `gh pr merge` 一敲下去，宿主会弹一个确认框，
  你点"允许"才真的合，点"拒绝"就停。跟 Claude Code 平时问你"要不要允许这条命令"是同一个框。
- **不让强推覆盖 main。** 这是少数几个能真把别人工作干掉的操作，任何分支上都拦。
- **把约定写进 CLAUDE.md 和 AGENTS.md。** hook 是事后拦，文档是事前讲。两边说的是同一套规则，
  agent 一开始就按规矩走，而不是撞了墙才知道。

## 它不替你做什么

说清楚比藏着好：

- **不会不问就开 PR、不问就合并。** 开 PR 前 agent 会提议，合并时宿主会弹框，两道都过了才动。
- **自动存档不等于替你写提交历史。** `wip:` 提交只是防丢，合并前该 squash 就 squash，
  整理完用 `git push --force-with-lease` 推自己的分支。
- **不是安全防护。** 它防的是顺手犯错，不防人故意绕过。真要挡住谁，
  得靠 GitHub / GitLab 上的分支保护（服务端），不是这里。

---

## 装

```bash
./install.sh /path/to/repo      # 省略路径 = 当前目录
```

一次把三家 agent 都装好，顺手往目标仓库的 `CLAUDE.md` 和 `AGENTS.md` 里追加一段带标记的约定。
重复装不会装重；**不会动你已有的其他配置和文档内容**，装之前自动备份成 `.bak`。

卸载：

```bash
./install.sh --uninstall /path/to/repo
```

## 接管

自动驾驶随时能切手动。确实需要在 main 上动手时：

```bash
touch <仓库>/.git/ALLOW_MAIN     # 持久。不进版本库，只影响你这台机器
ALLOW_MAIN=1 claude              # 或者只对这一次会话
```

不想再要了就 `rm <仓库>/.git/ALLOW_MAIN`。

不想要自动存档、自动推送：

```bash
git config autopilot.autocommit false
git config autopilot.autopush false
```

---

## 支持哪些 agent

| | hook 写到 | 约定写到 | 状态 |
|---|---|---|---|
| Claude Code | `.claude/settings.json` | `CLAUDE.md` | 官方文档，已实测触发 |
| Codex CLI | `.codex/hooks.json` | `AGENTS.md` | 官方文档，hook 可能需在 config.toml 打开开关，未实测 |
| Pi | `.pi/settings.json` | `AGENTS.md` | Claude 兼容层是第三方适配，首次用前先手测（见下） |

三家能共用同一份脚本，是因为它们的 hook 协议**是同构的**——都是 stdin 收事件 JSON、
stdout 回 `hookSpecificOutput.permissionDecision`。所以这里只有一份 `guard-branch.py`，
不是三份。

`permissionDecision` 有三档：`allow` 放行、`deny` 拦下、`ask` 弹确认框。合并 PR 用的是 `ask`。
Claude Code 三档都实测过；Codex / Pi 的 `ask` 按文档同构，未实测，首次用前建议在一个不要紧的 PR 上试一次。

首次手测（尤其 Pi）：

```bash
echo '{"cwd":"'$PWD'","tool_name":"Edit","tool_input":{}}' | python3 hooks/guard-branch.py
```

在 main 上应回一段 `"permissionDecision": "deny"` 的 JSON；在工作分支上应该没有输出。

---

## 技术细节

<details>
<summary>目录结构、规则表、可调项、怎么改严格程度</summary>

| 路径 | 说明 |
|---|---|
| `hooks/guard-branch.py` | 分支守卫 + 分支命名校验，跑在 PreToolUse 阶段 |
| `hooks/wrap-up.py` | 收工自动存档 + 推送 + 提示 + PR 提议，跑在 Stop 阶段 |
| `hooks/_common.py` | 两者共用：读 `git config autopilot.*` |
| `templates/*.json` | 三家的 hook 配置模板，`__HOOKS_DIR__` 装载时替换成绝对路径 |
| `templates/agent-rules.md` | 写进 CLAUDE.md / AGENTS.md 的那段约定 |
| `install.sh` | 装载 / 卸载，幂等 |

### 规则

| 规则 | 行为 |
|---|---|
| 强推 main/master | **任何分支都拦**（`--force-with-lease` 放行） |
| `gh pr merge` | **任何分支都弹确认框**（`permissionDecision: ask`），人点允许才执行 |
| `git switch -c` / `checkout -b` / `branch` 起的名字不合规 | **任何分支都拦**，回一句该怎么起 |
| 保护分支上 `Edit` / `Write` / `apply_patch` | **拦** |
| 保护分支上 `commit` `merge` `rebase` `push` `revert` `reset --hard` | **拦**（`cd x && git commit` 也拦得住，按 `&&` `;` `\|\|` 切段判断） |
| `git switch -c` / `status` / 读文件 | **放行**——补救动作不能被守卫自己堵死 |
| Stop 时工作分支有未提交改动 | `git add -A && git commit -m "wip: …"` |
| Stop 时工作分支领先远端 | `git push -u origin <分支>`，不带 force，推不上只提示 |
| Stop 时分支领先基准分支 | 提示提交数、文件数，建议先审 |
| Stop 时领先 ≥ N 个提交且没开 PR | 提示"若已到阶段性节点，建议向人提议提交 PR"（需装 `gh`） |

### 可调项

都是 `git config`，加 `--global` 就对所有仓库生效：

| 键 | 默认 | 说明 |
|---|---|---|
| `autopilot.protected` | `main master` | 保护分支，空格或逗号分隔 |
| `autopilot.base` | protected 里第一个存在的 | 收工提示拿哪条分支做比较基准 |
| `autopilot.branch-prefixes` | `feat fix chore docs refactor test` | 允许的分支前缀 |
| `autopilot.branch-pattern` | 由前缀生成 | 直接给正则，设了就忽略前缀 |
| `autopilot.autocommit` | `true` | 收工是否自动存档 |
| `autopilot.autopush` | `true` | 收工是否自动推送工作分支 |
| `autopilot.pr-nudge-after` | `3` | 领先多少个提交后开始提 PR 建议 |

### 严格程度

默认是**编辑闸门**：main 上连改文件都拦。

理由：`git switch -c` 会把未提交改动原样带到新分支，所以"改到一半被拦"完全无损，
代价只是多敲一条命令；而只拦 `commit` 要等改完二十个文件才报警，反馈太晚。

想换宽松版（main 上随便改，只在 commit 时拦）：注释掉 `guard-branch.py` 里
「规则 2：在保护分支上改文件」那一段。

### 已知的边界

- 判断分支用的是**会话所在仓库**，不跟着命令里的 `cd` 走。在 main 上编辑仓库外的文件也会被拦，
  开条分支就好。
- 命令按字符串匹配。`git log --grep commit`、`echo git commit` 这类会误拦，重新措辞即可。
- 自动存档用 `git add -A`，没进 `.gitignore` 的临时产物会被一起存进去。
- 自动推送需要本机已有推送凭据（ssh key 或 credential helper），没有就直接失败并提示，不会挂在密码提示上。
- PR 提议依赖 `gh` 已登录；`gh` 没装或没登录就不提，也不报错。

### 为什么不放网盘

`.git` 是成千上万个小文件加频繁改写的索引，走 Seafile / Dropbox 这类同步很容易冲坏。
本仓库刻意放在本地，版本历史靠 git remote 同步。

</details>
