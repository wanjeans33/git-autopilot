# git-autopilot

给 AI coding agent 装一个 **git 自动驾驶**。

装上之后，你只管用自然语言跟 agent 说要做什么。分支什么时候该开、叫什么名字、收工要不要存档、
什么时候该提 PR、合并前要不要先审，它替你把关。**你随时可以接管。**

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
  你点头它才 `gh pr create`。合并可以由 agent 执行，但 `gh pr merge` 一敲下去，Claude Code 会弹一个确认框，
  你点"允许"才真的合，点"拒绝"就停。跟 Claude Code 平时问你"要不要允许这条命令"是同一个框。
  Codex / Pi 的 hook 协议不支持弹框，那里 `gh pr merge` 会被直接拦下，由你自己在终端合。
- **不让强推覆盖 main。** 这是少数几个能真把别人工作干掉的操作，任何分支上都拦。
- **把约定讲在前面。** hook 是事后拦，约定是事前讲。两边说的是同一套规则，
  agent 一开始就按规矩走，而不是撞了墙才知道。

## 它不替你做什么

说清楚比藏着好：

- **不会不问就开 PR、不问就合并。** 开 PR 前 agent 会提议，合并时 Claude Code 会弹框（Codex / Pi 直接拦），
  两道都过了才动。
- **自动存档不等于替你写提交历史。** `wip:` 提交只是防丢，合并前该 squash 就 squash，
  整理完用 `git push --force-with-lease` 推自己的分支。
- **不是安全防护。** 它防的是顺手犯错，不防人故意绕过。真要挡住谁，
  得靠 GitHub / GitLab 上的分支保护（服务端），不是这里。

---

## 装

三条路，选一条。都是装一次管所有仓库，不用每个项目单独弄。

### 1. Claude Code：装插件（推荐）

在 Claude Code 里：

```
/plugin marketplace add wanjeans33/git-autopilot
/plugin install git-autopilot@git-autopilot
```

不需要 bash，不改任何仓库，Windows 直接用。插件自带 hook 和一份 skill，skill 就是下面那段约定，
agent 在 git 仓库里动手前会自己加载。

### 2. Codex / Pi，或者不想用插件：`install.py`

只依赖 Python 3.8+，三个系统一样：

```bash
python3 install.py --global          # 用户级，所有仓库生效（推荐）
python3 install.py /path/to/repo     # 或者只装到一个仓库
python3 install.py --uninstall --global
```

Windows 上用 `py install.py` 或 `python install.py`。老习惯 `./install.sh` 还在，它只是转调 `install.py`。

用户级装到 `~/.claude/settings.json`、`~/.codex/hooks.json`、`~/.pi/agent/settings.json`，
约定段落写进 `~/.claude/CLAUDE.md` 和 `~/.codex/AGENTS.md`。按仓库装则写进仓库里的同名文件。
重复装不会装重，**不会动你已有的其他配置和文档内容**，装之前自动备份成 `.bak`。

### 3. 某个仓库不想要

```bash
git config autopilot.enabled false
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

| | 装法 | 约定写到 | 状态 |
|---|---|---|---|
| Claude Code | 插件，或 `install.py` | skill，或 `CLAUDE.md` | 官方文档，deny / ask / Stop 都实测过 |
| Codex CLI | `install.py` | `AGENTS.md` | 官方文档明确 `ask` 不支持，合并 PR 改为 deny；非托管 hook 要先在 `/hooks` 里审阅并信任才会跑；未实测 |
| Pi | `install.py` | `AGENTS.md` | Claude 兼容层是第三方适配，按不支持 `ask` 处理（合并 PR 用 deny），全局路径按其文档推断，首次用前先手测（见下） |

三家能共用同一份脚本，是因为它们的 hook 协议**是同构的**——都是 stdin 收事件 JSON、
stdout 回 `hookSpecificOutput.permissionDecision`。所以这里只有一份 `guard-branch.py`，
不是三份。

`permissionDecision` 有三档：`allow` 放行、`deny` 拦下、`ask` 弹确认框。三档不是每家都认：
Claude Code 三档都实测过；Codex 官方文档写明 `ask` "parsed but not supported yet"，遇到会把 hook 记为失败、
报错、**然后照常执行工具**，等于放行。所以 `install.py` 给每家的 hook 命令都带 `--host <claude|codex|pi>`，
守卫只对 `claude` 发 `ask`，其他宿主（含没传 `--host` 的旧安装）对 `gh pr merge` 一律 `deny`，让人自己在终端合。
从旧版升级请重跑一次 `install.py`。

Codex 还有一道自己的门：非托管 hook 首次运行前，要在 Codex 里执行 `/hooks` 审阅并信任这条 hook 定义，
否则它根本不会跑。装完记得做这一步。

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
| `hooks/guard-branch.py` | 分支守卫 + 分支命名校验 + 合并确认，跑在 PreToolUse 阶段 |
| `hooks/wrap-up.py` | 收工自动存档 + 推送 + 提示 + PR 提议，跑在 Stop 阶段 |
| `hooks/_common.py` | 两者共用：读 `git config autopilot.*` |
| `hooks/py.sh` | 插件模式的启动器：依次找 `python3` / `python` / `py -3`，都没有就静默放行 |
| `hooks/hooks.json` | 插件的 hook 声明，路径用 `${CLAUDE_PLUGIN_ROOT}` |
| `.claude-plugin/` | 插件清单和 marketplace 清单 |
| `skills/git-autopilot/` | 插件带的 skill，内容就是那段约定 |
| `templates/*.json` | `install.py` 用的三家 hook 配置模板，`__PYTHON__` / `__HOOKS_DIR__` 装载时替换 |
| `templates/agent-rules.md` | `install.py` 写进 CLAUDE.md / AGENTS.md 的那段约定 |
| `install.py` | 装载 / 卸载，幂等，跨平台。`install.sh` 只是转调它 |

### 规则

| 规则 | 行为 |
|---|---|
| 强推 main/master | **任何分支都拦**（`--force-with-lease` 放行） |
| `gh pr merge` | **任何分支都拦下问人**：Claude Code 弹确认框（`ask`），Codex / Pi 不支持 `ask`，直接 `deny`，由人在终端合 |
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
| `autopilot.enabled` | `true` | 这个仓库要不要这套。用户级安装后用它关掉个别仓库 |
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
- `install.py` 把**运行它的那个 Python** 的绝对路径写进 hook。之后换掉或删掉这个 Python（比如 uv 管理的版本），
  守卫会静默失效。重跑一次 `install.py` 即可。插件模式没有这个问题，`py.sh` 每次现找。
- 插件和 `install.py` 同时装了会各跑一遍守卫，结果一样，只是多花几十毫秒。
- 插件的清单和 hook 声明按官方文档写，`hooks/py.sh` 启动器实测过；但 `/plugin install` 这条流程本身
  还没在干净机器上走过一遍。装完在 main 上随便 Edit 一下，被拦就是装好了。
- Windows 上 hook 由 Claude Code 通过 Git Bash 执行，`py.sh` 和 `install.py` 都按这个前提写，
  但 Windows 一次都没实测。有 Windows 的人试过请开 issue。

### 为什么不放网盘

`.git` 是成千上万个小文件加频繁改写的索引，走 Seafile / Dropbox 这类同步很容易冲坏。
本仓库刻意放在本地，版本历史靠 git remote 同步。

</details>
