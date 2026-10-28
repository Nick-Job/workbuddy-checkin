# Security Notes

本文是对以下三份代码的静态安全审计记录，不构成“绝对安全”保证。

## 审计对象

| 对象 | 版本/来源 | SHA-256 |
|---|---|---|
| `workbuddy-oauth-checkin.skill` | `https://szb123.com/files/workbuddy-oauth-checkin.skill` | `31b8dc821116ea95b5c4929eb87e5fbf08a1470f9ce42103c1210a2b4297c5f6` |
| `totorosir-workbuddy-checkin` | SkillHub `@user_3a370331`，v3.1.7 | `349e6bb7aa9e0510f6128f34615e053c726f3cf3cd4de63f8d8f30a1882e6a31` |
| 本仓库 | `Nick-Job/workbuddy-checkin` | 见 git commit |

## 结论摘要

- 原始薄层 `workbuddy-oauth-checkin`：只访问腾讯/CodeBuddy 官方端点，并调用本地执行器；没有发现硬编码的陌生 C2、`eval/exec`、远程下载或混淆载荷。
- 原始执行器 `totorosir-workbuddy-checkin`：没有发现明显的 token 外传路径，但包含多项高权限本地能力，属于“高风险但可解释”的实现，而不是低风险实现。
- 本仓库：不读取 WorkBuddy 客户端登录态、不扫描进程内存、不读取浏览器 Cookie、不做远程更新；只访问三个官方域名，且只读写自己的凭据文件。

## 原始项目的主要风险点

### 1. Windows 进程内存扫描（最高风险）

`totorosir-workbuddy-checkin/scripts/workbuddy_checkin.py` 在 WorkBuddy 5.6.2+ 加密登录态场景下，会：

- 用 `OpenProcess` / `ReadProcessMemory` 扫描运行中的 WorkBuddy 进程内存；
- 在内存中搜索 44 字符的 `atRestSecretKey` 候选；
- 用 `keyId` 校验候选，再解密 `auth.accessToken` / `auth.refreshToken`；
- 同时支持 DPAPI 解密和 `~/.workbuddy/keyblob` 相关路径。

这看起来是为了解决“客户端把密钥只放在内存里”的兼容性问题，但它的能力边界很大：能读取同一用户下 WorkBuddy 进程的任意可读内存。若安装包被篡改，这段能力可以被改造成读取其他内存内容。**这是我最不建议在主力机上直接运行原执行器的原因。**

### 2. 浏览器凭据收集书签

`templates/auth-snippet.js` 是一个浏览器 Console/书签脚本，会读取当前页面的：

- `localStorage` / `sessionStorage` 中的 `accessToken`、`token`、`jwt` 等字段；
- 当前页面的 `document.cookie`。

脚本本身只把结果写入剪贴板，没有发现网络发送代码。但它产出的就是账号会话凭据；如果用户把授权码粘贴到群聊、日志或第三方工具，就会造成凭据泄漏。这不是后门，但属于高风险授权方式。

### 3. 凭据落盘的短暂权限窗口

原执行器的 `save_rt()` / `save_cred()` 使用“先普通 `open(..., "w")` 写入，再 `chmod 0600`”的方式。若进程 umask 较宽松，文件在写入与 chmod 之间可能短暂可读。风险是本机其他用户，不是远程后门。

### 4. 命令行参数与当前目录

- `provision.py` 会把 refresh token 作为 `--setup-rt '<token>'` 的参数传给子进程，可能出现在进程列表 / shell 历史中。
- `workbuddy_oauth.sh` 把 OAuth state 和 Cookie jar 写到当前目录的 `.wb_oauth_state.json` / `.wb_oauth_cookies.txt`，权限取决于 umask。

### 5. 推送模块

`push_message.py` 支持钉钉、飞书、企业微信、PushPlus、邮件、短信、Telegram、Bark、Slack、通用 Webhook 等。审计看到它发送的是签到结果文本（时间、状态、积分、余额），没有发现把 `accessToken` / `refreshToken` 放进推送正文的代码。风险主要来自用户自己配置的 webhook 是否可信，以及配置文件中保存的推送密钥。

### 6. 未发现的项目

- 未发现 `eval` / `exec` / `pickle` / `marshal` 远程代码执行；
- 未发现硬编码的陌生域名或 IP；
- 未发现自动下载/自动更新逻辑；
- 未发现读取 `~/.ssh`、浏览器 `Cookies` / `Login Data`、keychain、钱包等路径的代码；
- 未发现把 token 拼进 URL 查询参数外发的代码（官方 OAuth/refresh 端点除外）。

## 本仓库的安全边界

本仓库是独立实现，不依赖上述执行器：

- 只访问 `copilot.tencent.com`、`www.codebuddy.cn`、`www.workbuddy.cn`；
- 不扫描进程内存、不调用 DPAPI、不读取 WorkBuddy 客户端登录态；
- 不读取浏览器 Cookie / localStorage；
- 不做远程更新、不动态下载代码；
- 唯一 `subprocess` 用途是 `crontab -l` / `crontab -` 安装定时任务；
- 凭据只写入 `~/.workbuddy/checkin-rt.json`，使用 `mkstemp` + `fsync` + `os.replace`，权限 0600；
- refresh token 轮换后立即回写，并用文件锁避免并发冲突；
- 所有对外输出只包含脱敏 token（前 6 位 + 后 4 位）；
- 默认不执行旅行写操作，需显式 `--travel auto`；
- 无第三方运行时依赖；CI 只运行离线单元测试。

## 残余风险

- 静态审计不能证明“绝对没有后门”。动态生成 URL、供应链篡改、安装包替换等情况需要哈希校验、签名校验或沙箱运行才能进一步排除。
- 本工具仍然需要保存 refresh token；能读取该文件的同用户进程可以接管账号。
- OAuth 授权链接包含 state，应在私聊中打开；不要在群里转发。
- 官方接口随时可能变化；工具失效不代表存在后门。

## 建议

1. 担心原项目时，优先使用本仓库，不要运行原执行器的本地登录态/内存扫描通道。
2. 如果必须使用原执行器，放在独立用户、容器或虚拟机中运行，并限制其可访问的文件。
3. 不要把 refresh token 粘贴到聊天、Issue、日志或 shell 历史；优先用 `--token-file` / `--token-stdin`。
4. 使用完原项目后，重新登录 WorkBuddy 客户端以轮换会话；必要时撤销旧令牌。
5. 对下载的 `.skill` / `.zip` 先核对 SHA-256，再解压审查。
