---
name: hermes-email-watchdog
description: 安装、个性化配置并运行多账号邮件助理；只读监控邮件，并可在用户逐次确认后创建提醒、回复或转发邮件。
version: 0.13.2
tags: [email, watchdog, notification, read-only, hermes, onboarding]
---

# Hermes Email Watchdog

Email Watchdog 是独立邮箱助理。监控、分析、附件获取始终只读；可选的邮件回复与转发是另一条默认关闭的能力，只有用户引用具体推送、审阅草稿并再次确认后才可发送。它不依赖 Weekly Briefing。

## 安装与配置对话

当用户要求安装、配置、迁移或启用 Email Watchdog 时，必须主动引导：

1. 运行 `hermes email-watchdog setup`，读取已检测账户、当前状态和 `unresolved`。
2. 若 `unresolved` 包含 `himalaya`，说明将下载经过版本固定和 SHA-256 校验的只读邮箱客户端到当前 Hermes profile；获得同意后运行 `hermes email-watchdog himalaya-install --yes`，再重新执行 `setup`。不要使用 `curl | sh`，也不要在未获同意时安装。
3. 使用当前 Hermes 会话作为通知目标。底层会读取 `HERMES_SESSION_PLATFORM` 与 `HERMES_SESSION_CHAT_ID`；Hook 也会保存待确认目标。
4. 若只检测到一个有效 Himalaya 配置，自动复用；不要询问已经可靠检测到的账户或路径。
5. 只逐项询问缺失的用户级信息：
   - 要监控哪些邮箱；
   - 仅行动项还是更广泛通知；
   - 附件自动下载/消息渠道转发偏好和大小上限；
   - 时区与提醒提前量；
   - 是否启用“草稿确认后回复”；
   - 每个邮箱的签名档，或明确选择无签名；
   - 模型偏好（无偏好时保留默认）。
6. 新账户必须使用外部凭据命令，例如 `pass`、`secret-tool`、`security`、`op`、`bw`、`gopass` 或受保护的环境变量。绝不询问、接收或复述邮箱密码、应用专用密码、令牌和密钥；禁止用带明文的 `echo`/`printf`。
7. 由 Agent 在后台构造设置数据，并依次调用：

   ```bash
   hermes email-watchdog onboarding-plan --input-json '<JSON>'
   hermes email-watchdog onboarding-apply --input-json '<JSON>'
   ```

   用户不应被要求创建或编辑 JSON。`plan` 用来说明将发生的变更；`apply` 原子写入，并以一封信列表进行只读验证，失败自动回滚。
8. 普通“安装”默认保持禁用；明确的“安装并启用”或“启用邮件监控”才构成启用授权。
9. 应用后运行 `doctor` 与 `run-once`。只报告脱敏结果，不暴露完整邮箱、聊天 ID、凭据命令或内部路径。
10. 只有只读验证和试运行通过后才运行 `enable`。恢复或升级时跳过已经验证的步骤，不重复要求登录。

自然对话与无人值守部署必须使用同一 `plan`/`apply` 内核，并产生相同的标准配置哈希。

## 强制保证

- 监控与验证只读；验证只能使用 `envelope list --page-size 1`。
- 提醒不能由邮件内容自动创建。用户必须引用推送并回复精确的 `提醒我`，多个截止时间必须回复编号。
- 回复命令必须是行首精确的 `@回复`，正文保持用户原文，只追加该邮箱签名；默认只回复发件人。
- 转发命令为 `@转发 收件邮箱`；可选的后续正文保持原样并追加签名，无正文则直接转发原邮件与附件。
- 回复和转发都必须先生成草稿，再引用草稿回复 `@确认发送`；`@取消` 放弃草稿。
- 引用发送成功通知并回复 `@召回` 可请求召回。只有邮箱服务商返回可验证结果时才能报告成功；SMTP/IMAP 删除已发送副本不属于召回。
- 回复与带正文的转发必须从同一个按邮箱配置的签名来源、同一套语义布局生成；已发送、已取消和结果不确定的草稿仍必须由插件消费并返回幂等状态，绝不能落入普通对话。
- `no-reply`、自动通知和高风险地址默认阻止回复；不提供归档、删除、移动、标记或设为已读操作。
- 未完成发信通道验证或未显式启用 `reply.outbound_enabled` 时，确认发送也必须失败关闭。
- 缺失或无效配置时调度器保持禁用。
- 安装不修改 Hermes 核心消息适配器。
- 默认卸载保留配置与引导状态；purge 必须显式确认并只删除插件自有数据。
- 渠道传输由 Hermes 适配器负责；微信引用动作需要 `hermes-wechat-enhance >=2.3.0` 提供可持久还原的标准化入站引用事件和通用 `@` 命令桥接。两个插件仍独立安装，WeChat Enhance 不导入或假定 Email Watchdog 存在。

## 数据归属

- Skill 源码：当前插件目录；
- 活动 Hook：当前 Hermes profile 的 `hooks/hermes-email-watchdog/`；
- 用户数据：当前 Hermes profile 的 `plugin-data/hermes-email-watchdog/`。

升级不得覆盖邮箱认证、通知目标、个性化策略、已处理索引、附件、日程或 outbox。
