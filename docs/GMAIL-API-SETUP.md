# Gmail API 后端（可选）

默认后端是 IMAP（himalaya）。Gmail 的 IMAP 接口存在可见性延迟——2026-11-07 实测：一封已投递到 Gmail 的邮件，**309 秒**后才出现在 IMAP 里；同一时刻 163/USTC 的可见延迟是秒级。对验证码这类时效邮件，这个延迟无法靠加大轮询频率解决。

`backend: gmail_api` 让某个账户改用 Gmail REST API 取信，绕过该延迟。

## 一次性准备（需要 Google 账号持有者操作）

1. 打开 [Google Cloud Console](https://console.cloud.google.com/)，新建（或选用）一个项目。
2. **API 和服务 → 库**，搜索并启用 **Gmail API**。
3. **API 和服务 → OAuth 同意屏幕**：类型选「外部」，填写应用名与支持邮箱；作用域添加
   `https://www.googleapis.com/auth/gmail.readonly`；把自己加成 **测试用户**（保持「测试」状态即可，不必发布）。
4. **API 和服务 → 凭据 → 创建凭据 → OAuth 客户端 ID**，应用类型选 **桌面应用**，下载 JSON。
5. 把 JSON 放到 NAS：
   `/opt/data/plugin-data/hermes-email-watchdog/gmail-api/client_secret.json`（权限 0600）。

## 授权（我来执行，你只需粘贴一次 URL）

沙箱里没有浏览器，所以走「复制回跳地址」的方式：

```bash
docker exec -it -e HERMES_EMAIL_WATCHDOG_STATE_ROOT=/opt/data/plugin-data/hermes-email-watchdog \
  -e EMAIL_GMAIL_API_PROXY=http://127.0.0.1:17898 \
  hermes-v0213-gateway python3 /opt/data/skills/hermes-email-watchdog/scripts/gmail_oauth_setup.py
```

它会打印 `OPEN_THIS_URL=...`：在你的浏览器里打开并同意，随后浏览器会跳到
`http://localhost:8080/?code=...`（页面打不开是正常的），把**完整地址**粘回终端即可。
成功后写入 `gmail-api/token.json`（0600，含 refresh token）。

## 启用

账户配置里加两项（`config.json` 的 `accounts`）：

```json
{ "label": "Gmail", "backend": "gmail_api", "api_proxy": "http://127.0.0.1:17898" }
```

- `backend`：默认 `imap`；设为 `gmail_api` 才走 REST。
- `api_proxy`：容器直连 `*.googleapis.com` 不通，必须经既有代理（NAS 上 `127.0.0.1:17898`）。

改完不需要重启容器：看门狗每轮重新加载脚本。

## 依赖与回滚

- 依赖：`google-api-python-client`、`google-auth-oauthlib`（容器内已具备）。
- 回滚：把 `backend` 改回 `imap` 即可，两条路径共用同一套去重、分析与投递逻辑。

## 已知边界

- 这是轮询（`messages.list`），不是 Pub/Sub 推送。API 的可见延迟仍需实测；若 API 也有分钟级延迟，再上 `users.watch` + Pub/Sub **pull** 订阅（不需要公网入口）。
- `gmail.readonly` 只读，不会改动邮箱状态。
