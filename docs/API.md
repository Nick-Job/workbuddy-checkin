# API Notes

本文记录本工具使用的端点与行为。接口由第三方服务提供，可能随时变化；本仓库不保证长期兼容。

## 域名

| 域名 | 用途 |
|---|---|
| `copilot.tencent.com` | OAuth state / token / refresh |
| `www.codebuddy.cn` | 签到、积分状态（`/v2`） |
| `www.workbuddy.cn` | 派猫猫旅行（不带 `/v2`） |

签到和旅行是两套域名，不能互换。

## 1. OAuth 设备授权

### 1.1 创建 state

```http
POST https://copilot.tencent.com/v2/plugin/auth/state?platform=CLI
Content-Type: application/json
Accept: application/json, text/plain, */*
X-Requested-With: XMLHttpRequest
Origin: https://www.codebuddy.cn
Referer: https://www.codebuddy.cn/
User-Agent: CLI/2.63.2 CodeBuddy/2.63.2

{}
```

成功响应：

```json
{
  "code": 0,
  "msg": "OK",
  "data": {
    "state": "uuid",
    "authUrl": "https://copilot.tencent.com/login?platform=CLI&state=uuid"
  }
}
```

响应会设置 Cookie（例如 `tgw_l7_route`）。**轮询必须复用同一份 Cookie**，否则可能一直返回 `11217`。

### 1.2 轮询 token

```http
GET https://copilot.tencent.com/v2/plugin/auth/token?state=<state>
```

- `code=11217`：用户尚未完成登录
- `code=11218`：state 过期/无效
- `code=0`：返回 `accessToken`、`refreshToken`、`expiresIn`、`domain`

state 约 10 分钟有效，成功后一次性作废。

## 2. 刷新 token

```http
POST https://copilot.tencent.com/v2/plugin/auth/token/refresh
Content-Type: application/json
X-Refresh-Token: <refreshToken>
X-Auth-Refresh-Source: plugin
X-Domain: copilot.tencent.com

{}
```

响应 `data` 包含新的 `accessToken` 和 `refreshToken`。**旧 refresh token 会轮换失效，必须立即回写本地**。本工具在刷新成功后、签到之前完成回写。

## 3. 签到

基础地址：`https://www.codebuddy.cn/v2`

### 3.1 状态

```http
POST /billing/meter/checkin-activity-status
Authorization: Bearer <accessToken>
Content-Type: application/json

{}
```

关键字段：

```json
{
  "code": 0,
  "data": {
    "today_checked_in": false,
    "streak_days": 12,
    "daily_credit": 100,
    "total_credits": 1340
  }
}
```

余额字段在不同版本可能不同，本工具按 `total_credits`、`total_credit`、`credits`、`balance` 等候选名逐层查找。

### 3.2 领取

```http
POST /billing/meter/daily-checkin
Authorization: Bearer <accessToken>
Content-Type: application/json

{}
```

成功：

```json
{"code": 0, "credit": 100, "streak_days": 13, "data": {"total_credits": 1440}}
```

已签到是幂等状态，常见返回：

```json
{"code": 10001, "msg": "今天已签到，请明天再来"}
```

本工具把 `code=10001` 或包含“已签到”的消息视为成功跳过，不视为错误。

## 4. 派猫猫旅行

基础地址：`https://www.workbuddy.cn`，路径不带 `/v2`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/activity/growth/buddy/travel/status` | 当前状态 |
| GET | `/activity/growth/buddy/travel/config` | 可选地点 |
| POST | `/activity/growth/buddy/travel/claim` | 领取已到达奖励 |
| POST | `/activity/growth/buddy/travel/depart` | 派遣，body `{"location_id": 1}` |

状态：

- `idle`：可派遣
- `traveling`：旅行中，`arrive_at - server_now` 为剩余秒数
- `arrived`：可领取

自动模式顺序：查询状态 → 若 `arrived` 先领取 → 若 `daily_limit_reached` 为假且状态为 `idle`，读取地点并随机派遣 → 再查一次最终状态。

只读模式只调用 `status` 和 `config`，不发送任何写请求。

## 5. 错误处理

| 情况 | 行为 |
|---|---|
| OAuth `11217` | 继续轮询 |
| OAuth `11218` | 提示 state 过期，重新登录 |
| refresh 非 0 | `status=need_login`，提示重新授权 |
| 签到 HTTP 非 2xx | 尝试解析响应体；仍失败则 `status=error` |
| 已签到 `10001` | `status=ok`，`action=skip_already_signed` |
| 旅行只读失败 | 标记 `travel.available=false`，不影响签到结论 |
| 旅行写失败 | 记入 `travel.log`，不影响签到结论 |

## 6. 安全边界

- 不在任何输出中打印 token 明文
- 凭据文件 0600，目录 0700
- 刷新和写盘使用文件锁，避免并发轮换
- OAuth 授权只在私聊进行，避免链接被他人先点
- 本工具只应操作使用者本人账号
