# Hermes Email Watchdog

Hermes 的只读多账号邮件助理。它持续阅读新邮件，判断邮件真正想表达什么、你是否需要处理，以及最适合在微信中怎样呈现；低价值邮件保持静默，有用附件可随通知直接转发。

## 功能

- 通过 Himalaya 只读导出原始 MIME；不会因为检查邮件而改变已读状态。
- 区分正文、引用、签名、图片占位符、列表退订信息和常见模板噪声。
- 根据邮件目的选择验证码、财务、事件、截止任务、要点、原文节选等不同呈现方式，不套固定摘要模板。
- 模型字段采用语义归一化而不是封闭字段白名单：未来出现新的描述字段或常见别名时会提取可理解的意图并忽略无害扩展，只有越权操作、无事实依据的关键信息和不可解析输出才会被拒绝。
- 默认复用 Hermes 中名为 `USTC` 的 OpenAI 兼容提供方，主模型为
  `deepseek-flash`，失败时回退到 `qwen3.6-chat`；自定义配置会在升级时保留。
- 验证码直接突出可复制的验证码和有效期；账户确认、报名、支付等邮件会提取真实可点击链接；任务邮件突出动作和截止时间。
- 默认只通知验证码、账户安全、发票、重要附件、紧急、高风险、有截止时间或明确需要行动的邮件。
- 普通通知、营销邮件和新闻简报仍会被记录和学习，但保持静默。
- PDF、图片、Office 文档等安全附件会下载到当前 profile，并通过微信的文件/图片接口发送；危险扩展名、空文件和超出大小限制的文件不会转发。
- 有明确截止时间的事项会写入持久日程和本地 iCalendar 文件，默认在提前 24 小时、1 小时通过同一可靠微信 outbox 提醒；重启不会丢失，提醒会注明“如已完成请忽略”。
- 微信通知采用紧凑 Markdown 卡片，按摘要、待办、时间、链接和附件分块；有足够结构化信息时不会再粘贴大段模板原文。
- 通知使用持久 outbox；失败重试不会阻塞下一轮收信。
- 配置变更采用锁、原子写入和失败回滚。

本插件不发送邮件，也不改变已读、标记、移动或删除状态。微信等消息传输由 Hermes 及对应通道插件负责。

## 要求

- Hermes `>=0.21.3,<0.22`
- Python 3.11+
- [Himalaya](https://github.com/pimalaya/himalaya) 可执行文件
- 邮箱密码通过系统密钥环、密码管理器或外部命令提供；不要写入插件配置

## 安装

```bash
hermes plugins install Awenforever/hermes-email-watchdog
hermes plugins enable hermes-email-watchdog
hermes email-watchdog install-runtime
```

让 Hermes 引导完成邮箱、Himalaya 配置、通知策略和附件策略后，可先预览再原子应用：

```bash
hermes email-watchdog onboarding-plan --input-json @setup.json
hermes email-watchdog onboarding-apply --input-json @setup.json
hermes email-watchdog doctor
```

引导只询问尚未解析的账户信息，并要求通过系统密钥环、密码管理器或环境变量命令读取凭据；不会要求在聊天或配置中填写明文密码。应用阶段只执行一封信的只读列表验证，失败会恢复原配置并保持禁用。

可选设置包括：主模型与 fallback、仅行动项或全部邮件通知、是否自动下载附件、是否转发到微信、附件大小上限、时区和提醒提前量。推荐保持默认值：`deepseek-flash` → `qwen3.6-chat`、仅行动项通知、自动下载并转发安全附件、单个附件最多 25 MiB、提前 24 小时和 1 小时提醒。

配置邮箱后，先做一次只读试运行：

```bash
hermes email-watchdog run-once
hermes email-watchdog status
```

确认分类和通知目标无误后再启用定时监控：

```bash
hermes email-watchdog enable
```

`enable` 会重新执行只读邮箱验证；账号尚未配置或验证失败时会拒绝启用并保持关闭。

## 数据与边界

配置、seen 索引、缓存、学习数据库、日程、状态和 outbox 位于当前 Hermes profile 的 `plugin-data/hermes-email-watchdog/`。安装和升级不会覆盖邮箱数据、认证、目标或个性化配置；从旧版升级时，仅在新位置没有对应文件的情况下迁移旧数据。升级器只会识别并迁移已发布版本的完整默认策略签名，迁移前自动备份，插件回滚时同步恢复；任一策略字段经过自定义都会原样保留。

邮件正文和下载附件可能包含敏感信息。请限制 profile 目录权限、加密备份，并为缓存和附件设置符合个人需求的保留周期。插件日志和通知不应输出邮箱密码、访问令牌或完整认证命令。

## 生产建议

- 首次接入使用专用测试邮件验证匹配、拆分、时区和期限。
- 保持“仅行动项通知”默认策略，按实际误报逐步调整。
- 定期检查失败 outbox、解析降级率和被静默的高风险样本。
- 升级前备份 plugin-data；升级后先执行 `run-once` 再恢复计划任务。

## License

[MIT](LICENSE)
