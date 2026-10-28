# WorkBuddy Check-in

[![ci](https://github.com/Nick-Job/workbuddy-checkin/actions/workflows/ci.yml/badge.svg)](https://github.com/Nick-Job/workbuddy-checkin/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.9%2B-3776AB)](https://www.python.org/)

一个独立实现的 WorkBuddy / CodeBuddy 每日签到与派猫猫旅行工具，同时也是一个可直接放进 Agent 技能目录的 Skill。

- 纯 Python 标准库，无第三方依赖
- OAuth 设备授权 + 长效令牌自动轮换
- 每日签到、积分/连签查询、派猫猫旅行闭环
- 凭据 0600 落盘、文件锁、防并发轮换
- 默认不执行旅行写操作，需显式 `--travel auto`
- 23 个离线单元测试，CI 覆盖 Python 3.9–3.13

> 非官方项目，与腾讯 / WorkBuddy / CodeBuddy 无隶属关系。仅用于本人账号的私有自动化。

## 为什么另做一版

原始技能是一个很薄的编排层，真正执行签到的是另一个私有执行器技能。本项目把那层依赖去掉，自己做完整实现：

| | 原始 `workbuddy-oauth-checkin` | 本项目 |
|---|---|---|
| 执行器 | 依赖 `totorosir-workbuddy-checkin` | 自带完整实现 |
| 语言 | Bash + Python 混用 | 单一 Python 包 |
| 令牌导入 | 命令行明文参数 | 支持 `--token-file` / `--token-stdin` |
| 并发保护 | 无 | 文件锁 + 原子写 |
| 旅行默认 | 自动写操作 | 默认关闭，显式开启 |
| 测试 | 无 | 23 个离线测试 + CI |
| 输出 | 人类可读 + JSON 混合 | 统一 JSON 契约 |

## 功能

- `login`：OAuth 授权、轮询、保存 refresh token；也支持导入已有令牌
- `run`：签到；可选只读旅行状态或自动派猫猫
- `status`：只查询，不领取
- `doctor`：检查 Python、凭据文件、权限，可选联网验证
- `cron`：安装 / 查看 / 移除每日定时任务
- `logout`：清除本机凭据

## 安装

### 从源码运行

```bash
git clone https://github.com/Nick-Job/workbuddy-checkin.git
cd workbuddy-checkin
PYTHONPATH=src python3 -m workbuddy_checkin --help
```

也可以直接用包装脚本：

```bash
./bin/wb-checkin --help
```

### pip / pipx

```bash
pipx install .
# 或
python3 -m pip install --user .
wb-checkin --help
```

要求 Python 3.9+，不需要 `curl`。

## 快速开始

### 1. 登录

推荐在私聊里执行。命令会打印授权链接，然后等待浏览器登录完成：

```bash
wb-checkin login
```

Agent 场景可以拆成两步，避免阻塞消息：

```bash
wb-checkin login --no-wait --json
# 把 auth_url 原样发给用户；用户完成后：
wb-checkin login --poll --json
```

已有 refresh token 时，优先从文件读取，避免进入 shell 历史：

```bash
wb-checkin login --token-file ~/.workbuddy/refresh-token.txt
# 或
cat token.txt | wb-checkin login --token-stdin
```

令牌保存到 `~/.workbuddy/checkin-rt.json`（POSIX 0600）。可用 `--state-file` 或 `WORKBUDDY_CHECKIN_HOME` 改路径。

### 2. 签到

```bash
# 只签到
wb-checkin run --json

# 签到 + 只读查看旅行状态
wb-checkin run --travel readonly --json

# 签到 + 派猫猫全自动（先领后派）
wb-checkin run --travel auto --json
```

### 3. 定时任务

```bash
wb-checkin cron install --time 09:00 --travel auto
wb-checkin cron show
wb-checkin cron remove
```

POSIX 使用 `crontab`；Windows 会打印任务计划程序命令。

## 输出契约

`run` / `status` / `doctor` 的 `--json` 输出是单个 JSON 对象。示例：

```json
{
  "ok": true,
  "status": "ok",
  "action": "clicked",
  "message": "签到成功",
  "points": 100,
  "balance": 1300,
  "streak_days": 5,
  "checkin": {
    "status_http": 200,
    "status_code": "0",
    "http": 200,
    "code": "0",
    "today_checked_in": true
  },
  "travel": {
    "mode": "auto",
    "available": true,
    "state": "traveling",
    "state_text": "旅行中",
    "location": "咖啡馆",
    "remaining_text": "2 小时 13 分",
    "log": ["已派出 Buddy 前往【咖啡馆】"]
  },
  "credential": {
    "configured": true,
    "source": "refresh",
    "masked": "eyJhbG...x9Qw",
    "rotated": true,
    "path": "/home/me/.workbuddy/checkin-rt.json"
  },
  "warnings": []
}
```

`credential.masked` 只用于排查，不是可用令牌。

退出码：

- `0`：成功
- `1`：业务失败或网络错误
- `2`：需要登录，或参数错误

## 安全设计

- refresh token 只保存在本机，文件权限 0600，目录 0700
- 原子写入：先写临时文件，`fsync` 后 `os.replace`
- 文件锁：避免两个进程同时刷新，导致 refresh token 轮换冲突
- 刷新成功后**立即**回写新 refresh token，再执行签到
- 所有输出不含 access token / refresh token 明文
- 错误信息只保留服务端 `msg`，不回显请求头
- OAuth 轮询复用签发 state 时的 Cookie，避免一直 `11217`
- 默认不执行旅行写操作

## 兼容旧凭据

本工具读取 `~/.workbuddy/checkin-rt.json` 里的 `rt` 字段，因此可以直接复用旧版技能保存的令牌。保存时会保留该字段，并附加 `source` / `saved_at` 等元数据。

## 开发与测试

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

测试使用本地 HTTP server 模拟 OAuth 端点，不访问真实服务。CI 在 Python 3.9–3.13 上运行同一套测试。

接口细节见 [docs/API.md](docs/API.md)。

## 已知限制

- 接口行为来自公开资料和实测，上游可能随时调整；失败时请先看 `doctor` 和 `status`。
- 旅行奖励与派遣规则由服务端决定，本工具只按状态执行，不保证收益。
- refresh token 会轮换；如果服务端返回新 token 但本地写入失败，下一次需要重新登录。
- 不支持批量账号、账号池或对外 API 转售。

## 免责声明

本项目仅用于本人授权账号的私有自动化。使用非官方客户端或自动化可能违反目标平台服务条款，账号风控、封禁等风险由使用者自行承担。请勿用于批量注册、账号池、代签、售卖额度或其他滥用场景。
