---
name: workbuddy-checkin
description: 独立实现的 WorkBuddy/CodeBuddy 每日签到与派猫猫旅行工具。当用户说「WorkBuddy 签到 / CodeBuddy 签到 / 每日自动签到 / 帮我签个到 / 派猫猫旅行 / 领旅行奖励 / 配置签到定时任务 / 刷新令牌 / 重新授权 WorkBuddy」时使用。不依赖任何第三方执行器技能，只用 Python 标准库。
---

# WorkBuddy Check-in

这是一个自包含的 WorkBuddy/CodeBuddy 签到技能：OAuth 授权、refresh token 轮换、每日签到、派猫猫旅行和 cron 安装都在本仓库内完成。

## 安全红线

- 只处理用户**本人账号**；不要代输账号密码，不要让用户在群里发令牌。
- 令牌等同账号钥匙。**不要**把 `refreshToken`、`accessToken` 原文写进聊天、日志、命令输出或提交记录。
- 优先用 OAuth 登录。需要导入已有令牌时，用 `--token-file` 或 `--token-stdin`，不要用会进入 shell 历史的 `--token`。
- OAuth 只在私聊里发起。群聊中让用户单独私信，避免授权链接被其他人先点。
- 默认 `run` 不执行旅行写操作；需要派猫猫时显式加 `--travel auto`。

## 运行方式

从技能根目录运行：

```bash
PYTHONPATH=src python3 -m workbuddy_checkin <command>
```

或安装后使用：

```bash
pipx install .
wb-checkin <command>
```

本机已有旧版 `~/.workbuddy/checkin-rt.json` 时可直接复用，无需重新授权。

## 首次授权

推荐两阶段流程，适合 Agent 在聊天里转交链接：

```bash
PYTHONPATH=src python3 -m workbuddy_checkin login --no-wait --json
```

把输出里的 `auth_url` **原样**发给用户。用户完成浏览器登录后，在同一轮里继续执行（会阻塞等待）：

```bash
PYTHONPATH=src python3 -m workbuddy_checkin login --poll --json
```

也可以一步完成：`login` 会先打印链接，再等待用户登录。链接约 10 分钟有效；过期后重新执行 `login`。

已有 refresh token 时：

```bash
PYTHONPATH=src python3 -m workbuddy_checkin login --token-file /path/to/token.txt
```

## 日常使用

```bash
# 只签到（默认不碰旅行）
PYTHONPATH=src python3 -m workbuddy_checkin run --json

# 签到 + 只读查看旅行状态
PYTHONPATH=src python3 -m workbuddy_checkin run --travel readonly --json

# 签到 + 派猫猫全自动（先领后派）
PYTHONPATH=src python3 -m workbuddy_checkin run --travel auto --json

# 只查状态，不领取
PYTHONPATH=src python3 -m workbuddy_checkin status --json
```

输出是单个 JSON 对象。向用户汇报时只说：签到结果、积分、余额、连签天数、旅行状态；不要输出 `credential` 里的任何令牌。

## 定时任务

```bash
PYTHONPATH=src python3 -m workbuddy_checkin cron install --time 09:00 --travel auto
PYTHONPATH=src python3 -m workbuddy_checkin cron show
PYTHONPATH=src python3 -m workbuddy_checkin cron remove
```

Windows 请用任务计划程序，`cron` 命令会打印可直接使用的命令。安装前先 `cron show` 检查是否已有任务。

## 结果字段

- `status`: `ok` / `error` / `need_login`
- `action`: `clicked`（本次签到成功）/ `skip_already_signed` / `status` / `checkin_failed` / `refresh_failed` 等
- `points`: 本次获得积分
- `balance`: 当前积分余额
- `streak_days`: 连续签到天数
- `travel`: 旅行状态；`mode` 为 `readonly` 或 `auto`
- `warnings`: 非致命问题

退出码：`0` 成功，`1` 业务/网络失败，`2` 需要登录或参数错误。

## 排障

| 症状 | 原因 | 处理 |
|---|---|---|
| `need_login` / 刷新失败 | refresh token 失效或被轮换后未保存 | 重新 `login` |
| 轮询一直 pending | 用户未完成登录 / state 过期 / Cookie 丢失 | 确认登录完成；超过 10 分钟重新 `login` |
| 签到 404 | 请求打到了错误域名 | 本工具已固定签到域为 `www.codebuddy.cn`，不要手改 |
| 旅行 404 | 旅行接口在 `www.workbuddy.cn` 且不带 `/v2` | 使用本工具的 `--travel` 参数 |
| `no active buddy` | 账号没有活跃 Buddy | 正常现象，不影响签到 |
| 凭据文件权限不是 0600 | 手工拷贝或旧文件 | `doctor` 会提示；重新 `login` 或 `chmod 600` |

## 不要做

- 不要并发运行多个 `run`；refresh token 会轮换，并发会导致互相失效。工具内部有文件锁，但定时任务仍应避免重叠。
- 不要把本工具用于批量账号、账号池、代签或对外售卖。
- 不要修改 `docs/API.md` 里的域名和路径去“试试看”；签到和旅行是两套域。
