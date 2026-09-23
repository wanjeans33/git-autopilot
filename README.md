# git-autopilot

给 AI coding agent 装一个 **git 自动驾驶**。

装上之后，你只管用自然语言跟 agent 说要做什么。分支什么时候该开、合并前要不要先审，
它替你把关。**你随时可以接管。**

给谁用：不想管 git、也没必要学 git 的人。装完就不用再操心了。

---

## 它替你做什么

- **不让在 main 上直接干活。** 你（或 agent）一动手改文件，它先拦下来，提醒开一条分支。
  未提交的改动会原样带到新分支，**不会丢**，接着做就行。
- **不让强推覆盖 main。** 这是少数几个能真把别人工作干掉的操作，任何分支上都拦。
- **每次收工提醒一句：** 这条分支已经攒了多少提交、多少文件，要不要先审一遍再合。

## 它不替你做什么

说清楚比藏着好：

- **不会自动提交、自动合并。** 判断时机它管，按不按下去你说了算。
- **不是安全防护。** 它防的是顺手犯错，不防人故意绕过。真要挡住谁，
  得靠 GitHub / GitLab 上的分支保护（服务端），不是这里。

---

## 装

```bash
./install.sh /path/to/repo      # 省略路径 = 当前目录
```

一次把三家 agent 都装好。重复装不会装重；**不会动你已有的其他配置**，装之前自动备份成 `.bak`。

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

---

## 支持哪些 agent

| | 配置写到 | 状态 |
|---|---|---|
| Claude Code | `.claude/settings.json` | 官方文档，已实测触发 |
| Codex CLI | `.codex/hooks.json` | 官方文档 |
| Pi | `.pi/settings.json` | Claude 兼容层是第三方适配，首次用前先手测（见下） |

三家能共用同一份脚本，是因为它们的 hook 协议**是同构的**——都是 stdin 收事件 JSON、
stdout 回 `hookSpecificOutput.permissionDecision`。所以这里只有一份 `guard-branch.py`，
不是三份。

首次手测（尤其 Pi）：

```bash
echo '{"cwd":"'$PWD'","tool_name":"Edit","tool_input":{}}' | python3 hooks/guard-branch.py
```

在 main 上应回一段 `"permissionDecision": "deny"` 的 JSON；在工作分支上应该没有输出。

---

## 技术细节

<details>
<summary>目录结构、规则表、怎么改严格程度</summary>

| 路径 | 说明 |
|---|---|
| `hooks/guard-branch.py` | 分支守卫，跑在 PreToolUse 阶段 |
| `hooks/nudge-review.py` | 收工提示，跑在 Stop 阶段 |
| `templates/*.json` | 三家的配置模板，`__HOOKS_DIR__` 装载时替换成绝对路径 |
| `install.sh` | 装载 / 卸载，幂等 |

### 四条规则

| 规则 | 行为 |
|---|---|
| 保护分支上 `Edit` / `Write` / `apply_patch` | **拦** |
| 保护分支上 `commit` `merge` `rebase` `push` `revert` `reset --hard` | **拦**（`cd x && git commit` 也拦得住，按 `&&` `;` `\|\|` 切段判断） |
| 强推 main/master | **任何分支都拦**（`--force-with-lease` 放行） |
| `git switch -c` / `status` / 读文件 | **放行**——补救动作不能被守卫自己堵死 |

保护分支默认 `main` / `master`，改 `guard-branch.py` 顶部的 `PROTECTED`。

### 严格程度

默认是**编辑闸门**：main 上连改文件都拦。

理由：`git switch -c` 会把未提交改动原样带到新分支，所以"改到一半被拦"完全无损，
代价只是多敲一条命令；而只拦 `commit` 要等改完二十个文件才报警，反馈太晚。

想换宽松版（main 上随便改，只在 commit 时拦）：注释掉 `guard-branch.py` 里
「规则 1：在保护分支上改文件」那一段。

### 为什么不放网盘

`.git` 是成千上万个小文件加频繁改写的索引，走 Seafile / Dropbox 这类同步很容易冲坏。
本仓库刻意放在本地，版本历史靠 git remote 同步。

</details>
